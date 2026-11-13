import logging
import math
import os
import re
from typing import Any, Dict, Literal, Optional, Set
from urllib.parse import urlparse

import requests
from pydantic import BaseModel, Field, PrivateAttr, field_validator, model_validator

logger = logging.getLogger("SmartBuildingVoiceGateway.HA")

# ---------------------------------------------------------------------------
# Home Assistant Constants & Docker Floor Endpoints
# ---------------------------------------------------------------------------
HA_OFFLINE_PERSIAN_MESSAGE = "ارتباط با سیستم هوشمند این طبقه قطع است و در حال حاضر دسترسی ندارم."

ALLOWED_DOMAINS: Set[str] = {"light", "switch", "climate", "cover", "fan"}
ALLOWED_SERVICES_BY_DOMAIN: Dict[str, Set[str]] = {
    "light": {"turn_on", "turn_off", "toggle"},
    "switch": {"turn_on", "turn_off", "toggle"},
    "cover": {"turn_on", "turn_off", "toggle", "open_cover", "close_cover", "stop_cover"},
    "fan": {"turn_on", "turn_off", "toggle", "set_percentage"},
    "climate": {"turn_on", "turn_off", "toggle", "set_temperature", "set_hvac_mode"},
}
FORBIDDEN_TARGET_KEYS: Set[str] = {
    "entity_id",
    "area_id",
    "device_id",
    "floor_id",
    "label_id",
    "target",
}
SAFE_SERVICE_DATA_KEYS: Set[str] = {
    "temperature",
    "brightness",
    "brightness_pct",
    "hvac_mode",
    "percentage",
    "position",
}


def parse_allowed_entities(raw_value: str) -> Set[str]:
    return {item.strip().lower() for item in raw_value.split(",") if item.strip()}


def get_allowed_entities() -> Set[str]:
    return parse_allowed_entities(os.getenv("ALLOWED_ENTITIES", ""))


def get_ha_instance_config(floor: int) -> Dict[str, str]:
    """Resolves live URL and Long-Lived Access Token for Floor 1 (:8124) or Floor 2 (:8125)."""
    if floor == 1:
        url = os.getenv("HA_FLOOR1_URL", "http://localhost:8124").strip() or "http://localhost:8124"
        token = (
            os.getenv("HA_TOKEN_FLOOR1", "").strip()
            or os.getenv("HA_FLOOR1_TOKEN", "").strip()
        )
        return {"url": url, "token": token}
    if floor == 2:
        url = os.getenv("HA_FLOOR2_URL", "http://localhost:8125").strip() or "http://localhost:8125"
        token = (
            os.getenv("HA_TOKEN_FLOOR2", "").strip()
            or os.getenv("HA_FLOOR2_TOKEN", "").strip()
        )
        return {"url": url, "token": token}
    raise ValueError(f"Unsupported Home Assistant floor: {floor}")


class HAServiceError(Exception):
    """Raised when a floor's Home Assistant container is offline or rejects a command."""

    def __init__(
        self,
        detail: str,
        status_code: int = 503,
        error_code: str = "HA_UNREACHABLE",
        floor: Optional[int] = None,
        floor_port: Optional[int] = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.error_code = error_code
        self.floor = floor
        self.floor_port = floor_port


# ---------------------------------------------------------------------------
# Strict Pydantic Schema for LLM Home Assistant Commands
# ---------------------------------------------------------------------------
class LLMCommandSchema(BaseModel):
    floor: Literal[1, 2] = Field(description="Target floor number: 1 or 2")
    domain: Literal["light", "switch", "climate", "cover", "fan"] = Field(
        description="Home Assistant domain: light, switch, climate, cover, fan"
    )
    service: str = Field(min_length=1, description="Home Assistant service")
    entity_id: str = Field(min_length=1, description="Full Home Assistant entity_id, e.g., light.living_room")
    service_data: Optional[Dict[str, Any]] = Field(
        default_factory=dict, description="Safe Home Assistant parameters like temperature"
    )
    ack_message: str = Field(min_length=1, description="Immediate natural Persian confirmation message")
    success_message: str = Field(min_length=1, description="Natural Persian post-execution spoken response")
    _model_used: Optional[str] = PrivateAttr(default=None)

    @field_validator("service", "entity_id", "ack_message", "success_message", mode="before")
    @classmethod
    def _strip_required_strings(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @model_validator(mode="after")
    def _validate_home_assistant_command(self) -> "LLMCommandSchema":
        allowed_services = ALLOWED_SERVICES_BY_DOMAIN.get(self.domain, set())
        if self.service not in allowed_services:
            raise ValueError(f"Service '{self.service}' is not permitted for domain '{self.domain}'.")

        if not re.fullmatch(rf"^{re.escape(self.domain)}\.[a-z0-9_]+$", self.entity_id):
            raise ValueError(
                f"entity_id '{self.entity_id}' must match '{self.domain}.<object_id>' (lowercase alphanumeric/underscore)."
            )

        allowed_entities = get_allowed_entities()
        if allowed_entities and self.entity_id not in allowed_entities:
            raise ValueError(f"entity_id '{self.entity_id}' is not in ALLOWED_ENTITIES.")

        if self.service_data is None:
            self.service_data = {}
        elif not isinstance(self.service_data, dict):
            raise ValueError("service_data must be a JSON object.")

        sanitized_data: Dict[str, Any] = {}
        for key, val in self.service_data.items():
            if not isinstance(key, str):
                raise ValueError("service_data keys must be strings.")
            if key in FORBIDDEN_TARGET_KEYS:
                raise ValueError(f"Targeting key '{key}' is forbidden in service_data.")
            if key not in SAFE_SERVICE_DATA_KEYS:
                raise ValueError(f"Unsupported key '{key}' in service_data.")
            if not isinstance(val, (int, float, str, bool)):
                raise ValueError(f"service_data['{key}'] must be a scalar primitive.")
            if isinstance(val, float) and not math.isfinite(val):
                raise ValueError(f"service_data['{key}'] must be a finite number.")
            if key == "temperature":
                if isinstance(val, bool) or not isinstance(val, (int, float)):
                    raise ValueError("service_data['temperature'] must be numeric.")
                temp_val = float(val)
                if not (5.0 <= temp_val <= 40.0):
                    raise ValueError("service_data['temperature'] must be between 5.0 and 40.0.")
            if isinstance(val, str):
                cleaned_str = val.strip()
                if not cleaned_str or len(cleaned_str) > 64 or re.search(r"[\r\n\x00-\x1f\x7f]", cleaned_str):
                    raise ValueError(f"service_data['{key}'] contains invalid string content.")
                sanitized_data[key] = cleaned_str
            else:
                sanitized_data[key] = val

        self.service_data = sanitized_data
        return self


# ---------------------------------------------------------------------------
# Home Assistant Execution & Health Check (No Fake Simulation)
# ---------------------------------------------------------------------------
def execute_home_assistant_service(
    cmd: LLMCommandSchema, allowed_floors: Optional[Set[int]] = None
) -> Dict[str, Any]:
    """Dispatches real REST API call to the target floor's Home Assistant Docker container."""
    if allowed_floors is not None and cmd.floor not in set(allowed_floors):
        raise PermissionError(f"Floor {cmd.floor} is forbidden for the authenticated user.")

    ha_target = get_ha_instance_config(cmd.floor)
    base_url = ha_target["url"].rstrip("/")
    token = ha_target["token"]
    parsed_url = urlparse(base_url)
    if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
        raise ValueError(f"Invalid Home Assistant URL configured for floor {cmd.floor}.")
    floor_port = parsed_url.port or (8125 if cmd.floor == 2 else 8124)

    if cmd.domain not in ALLOWED_DOMAINS:
        raise ValueError(f"Disallowed Home Assistant domain: {cmd.domain}")
    if cmd.service not in ALLOWED_SERVICES_BY_DOMAIN.get(cmd.domain, set()):
        raise ValueError(f"Disallowed Home Assistant service '{cmd.service}' for domain '{cmd.domain}'")
    if not re.fullmatch(rf"^{re.escape(cmd.domain)}\.[a-z0-9_]+$", cmd.entity_id):
        raise ValueError(f"Invalid Home Assistant entity_id: {cmd.entity_id}")

    allowed_entities = get_allowed_entities()
    if allowed_entities and cmd.entity_id not in allowed_entities:
        raise ValueError(f"Entity '{cmd.entity_id}' is not in ALLOWED_ENTITIES")

    endpoint = f"{base_url}/api/services/{cmd.domain}/{cmd.service}"
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload: Dict[str, Any] = {"entity_id": cmd.entity_id}
    if cmd.service_data:
        payload.update(
            {
                k: v
                for k, v in cmd.service_data.items()
                if k not in FORBIDDEN_TARGET_KEYS and k in SAFE_SERVICE_DATA_KEYS
            }
        )
    payload["entity_id"] = cmd.entity_id

    logger.info(f"Dispatching HA request -> Floor {cmd.floor}: POST {endpoint} | Payload: {payload}")

    try:
        resp = requests.post(
            endpoint,
            headers=headers,
            json=payload,
            timeout=5,
            allow_redirects=False,
        )
    except requests.exceptions.RequestException as exc:
        logger.error(f"Home Assistant unreachable on Floor {cmd.floor} ({base_url}): {exc}")
        raise HAServiceError(
            detail=HA_OFFLINE_PERSIAN_MESSAGE,
            status_code=503,
            error_code="HA_UNREACHABLE",
            floor=cmd.floor,
            floor_port=floor_port,
        ) from exc

    if resp.status_code in (401, 403):
        logger.warning(f"Home Assistant auth failure on Floor {cmd.floor}: HTTP {resp.status_code}")
        raise HAServiceError(
            detail=f"احراز هویت با سیستم هوشمند طبقه {cmd.floor} ناموفق بود (توکن نامعتبر یا تنظیم‌نشده).",
            status_code=502,
            error_code="HA_UNAUTHORIZED",
            floor=cmd.floor,
            floor_port=floor_port,
        )

    if resp.status_code >= 300:
        logger.warning(f"Home Assistant HTTP error on Floor {cmd.floor}: HTTP {resp.status_code}")
        raise HAServiceError(
            detail=f"خطا در اجرای فرمان در سیستم هوشمند طبقه {cmd.floor} (کد وضعیت {resp.status_code}).",
            status_code=502,
            error_code="HA_EXECUTION_ERROR",
            floor=cmd.floor,
            floor_port=floor_port,
        )

    return {
        "ok": True,
        "status_code": resp.status_code,
        "floor_port": floor_port,
        "url": base_url,
    }


def check_ha_floor_health(floor: int, timeout: float = 1.5) -> Dict[str, Any]:
    """Probes a floor's Home Assistant Docker container via GET /api/."""
    cfg = get_ha_instance_config(floor)
    base_url = cfg["url"].rstrip("/")
    token = cfg["token"]
    parsed_url = urlparse(base_url)
    port = parsed_url.port or (8125 if floor == 2 else 8124)

    headers: Dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = requests.get(
            f"{base_url}/api/",
            headers=headers,
            timeout=timeout,
            allow_redirects=False,
        )
        # 200 = API ready + token valid; 401 = container online, token missing/invalid
        is_online = resp.status_code < 500
        return {
            "floor": floor,
            "port": port,
            "url": base_url,
            "online": is_online,
            "auth_ok": resp.status_code == 200,
            "token_configured": bool(token),
            "status_code": resp.status_code,
            "detail": (
                "متصل و احراز هویت شده"
                if resp.status_code == 200
                else (f"آنلاین (کد وضعیت {resp.status_code})" if is_online else f"خطای سرور {resp.status_code}")
            ),
        }
    except requests.exceptions.RequestException:
        return {
            "floor": floor,
            "port": port,
            "url": base_url,
            "online": False,
            "auth_ok": False,
            "token_configured": bool(token),
            "status_code": None,
            "detail": "کانتینر آفلاین یا غیرقابل دسترس",
        }
