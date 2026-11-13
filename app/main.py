import asyncio
import hashlib
import inspect
import json
import logging
import math
import os
import re
import secrets
import shutil
import tempfile
import threading
import wave
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from dotenv import load_dotenv

# Load .env before reading module configuration
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

import edge_tts
import ollama
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from faster_whisper import WhisperModel
from pydantic import BaseModel, Field, ValidationError, field_validator
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

try:
    from app.auth import (
        MAX_PASSWORD_LENGTH,
        MIN_PASSWORD_LENGTH,
        NO_STORE_HEADERS,
        AuthUser,
        GatewayAuthError,
        authenticate_bearer_header,
        build_error_response,
        check_bearer_auth,
        check_client_floor_override,
        check_login_rate_limit,
        clear_login_failures,
        create_access_token,
        detect_explicit_floors,
        ensure_users_file,
        format_floor_forbidden_detail,
        get_user_registry,
        list_users_public,
        record_login_failure,
        request_password_reset_otp,
        require_admin_user,
        require_authenticated_user,
        reset_password_with_otp,
        sanitize_for_log,
        update_user_email,
        update_user_password,
        validate_startup_auth_config,
        verify_password_pbkdf2,
        verify_password_reset_otp,
    )
    from app.ha_client import (
        HA_OFFLINE_PERSIAN_MESSAGE,
        HAServiceError,
        LLMCommandSchema,
        execute_home_assistant_service,
    )
    from app.monitor import collect_admin_system_stats
except ImportError:
    try:
        from auth import (
            MAX_PASSWORD_LENGTH,
            MIN_PASSWORD_LENGTH,
            NO_STORE_HEADERS,
            AuthUser,
            GatewayAuthError,
            authenticate_bearer_header,
            build_error_response,
            check_bearer_auth,
            check_client_floor_override,
            check_login_rate_limit,
            clear_login_failures,
            create_access_token,
            detect_explicit_floors,
            ensure_users_file,
            format_floor_forbidden_detail,
            get_user_registry,
            list_users_public,
            record_login_failure,
            request_password_reset_otp,
            require_admin_user,
            require_authenticated_user,
            reset_password_with_otp,
            sanitize_for_log,
            update_user_email,
            update_user_password,
            validate_startup_auth_config,
            verify_password_pbkdf2,
            verify_password_reset_otp,
        )
        from ha_client import (
            HA_OFFLINE_PERSIAN_MESSAGE,
            HAServiceError,
            LLMCommandSchema,
            execute_home_assistant_service,
        )
        from monitor import collect_admin_system_stats
    except ImportError:
        from .auth import (
            MAX_PASSWORD_LENGTH,
            MIN_PASSWORD_LENGTH,
            NO_STORE_HEADERS,
            AuthUser,
            GatewayAuthError,
            authenticate_bearer_header,
            build_error_response,
            check_bearer_auth,
            check_client_floor_override,
            check_login_rate_limit,
            clear_login_failures,
            create_access_token,
            detect_explicit_floors,
            ensure_users_file,
            format_floor_forbidden_detail,
            get_user_registry,
            list_users_public,
            record_login_failure,
            request_password_reset_otp,
            require_admin_user,
            require_authenticated_user,
            reset_password_with_otp,
            sanitize_for_log,
            update_user_email,
            update_user_password,
            validate_startup_auth_config,
            verify_password_pbkdf2,
            verify_password_reset_otp,
        )
        from .ha_client import (
            HA_OFFLINE_PERSIAN_MESSAGE,
            HAServiceError,
            LLMCommandSchema,
            execute_home_assistant_service,
        )
        from .monitor import collect_admin_system_stats

try:
    from piper import PiperVoice
except ImportError:
    PiperVoice = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Logging & Core Configuration
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)
logger = logging.getLogger("SmartBuildingVoiceGateway")

WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "base").strip() or "base"
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:4b").strip() or "qwen3.5:4b"
OLLAMA_FALLBACK_ENABLED = os.getenv("OLLAMA_FALLBACK_ENABLED", "false").strip().lower() == "true"
OLLAMA_LIST_TIMEOUT = float(os.getenv("OLLAMA_LIST_TIMEOUT", "5.0"))
OLLAMA_CHAT_TIMEOUT = float(os.getenv("OLLAMA_CHAT_TIMEOUT", "60.0"))

TTS_VOICE = os.getenv("TTS_VOICE", "fa-IR-DilaraNeural").strip() or "fa-IR-DilaraNeural"
TTS_TIMEOUT_SECONDS = float(os.getenv("TTS_TIMEOUT_SECONDS", "12.0"))
PIPER_MODEL_NAME = os.getenv("PIPER_MODEL_NAME", "fa_IR-amir-medium").strip() or "fa_IR-amir-medium"
PIPER_MODELS_DIR = Path(os.getenv("PIPER_MODELS_DIR", str(BASE_DIR / "storage" / "models")))
TTS_CACHE_DIR = Path(os.getenv("TTS_CACHE_DIR", str(BASE_DIR / "storage" / "cache")))

TTS_UNAVAILABLE_MESSAGE = (
    "پاسخ متنی آماده است؛ تولید صدای پاسخ با سرویس آنلاین و موتور آفلاین در دسترس نبود."
)

MAX_AUDIO_UPLOAD_BYTES = int(os.getenv("MAX_AUDIO_UPLOAD_BYTES", str(10 * 1024 * 1024)))
ALLOWED_AUDIO_SUFFIXES = {".webm", ".wav", ".ogg", ".mp3", ".m4a", ".flac", ".opus"}
MAX_CACHED_TTS_FILES = int(os.getenv("MAX_CACHED_TTS_FILES", "24"))

CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",")
    if origin.strip()
]
CORS_ALLOW_CREDENTIALS = (
    os.getenv("CORS_ALLOW_CREDENTIALS", "false").strip().lower() == "true"
    and "*" not in CORS_ORIGINS
)

TEMP_AUDIO_DIR = Path(tempfile.gettempdir()) / "smart_building_audio"

# Pre-cached common Persian error responses stored on disk
COMMON_ERROR_TTS_PHRASES: Dict[str, str] = {
    "ha_offline": HA_OFFLINE_PERSIAN_MESSAGE,
    "ollama_offline": "سرویس هوش مصنوعی اولاما در دسترس نیست. لطفاً وضعیت سرور را بررسی کنید.",
    "floor_forbidden": "شما مجاز به کنترل تجهیزات این طبقه نیستید.",
    "empty_transcript": "صدایی تشخیص داده نشد. لطفاً واضح‌تر صحبت کنید.",
    "stt_error": "خطا در تبدیل گفتار به متن. لطفاً دوباره صحبت کنید.",
}


def _ensure_temp_audio_dir(target_dir: Optional[Path] = None) -> Path:
    resolved_target = TEMP_AUDIO_DIR if target_dir is None else target_dir
    if resolved_target.is_symlink():
        raise RuntimeError("Temporary audio directory must not be a symbolic link.")
    resolved_target.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(resolved_target, 0o700)
    return resolved_target


_ensure_temp_audio_dir()
TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)

MAX_AUDIO_OWNER_ENTRIES = max(MAX_CACHED_TTS_FILES * 4, 200)
_AUDIO_OWNER_BY_ID: "OrderedDict[str, str]" = OrderedDict()
_audio_owner_lock = threading.Lock()

whisper_model: Optional[WhisperModel] = None
whisper_lock = threading.Lock()

piper_voice: Optional[Any] = None
piper_lock = threading.Lock()
_piper_load_attempted = False

_DEFAULT_OLLAMA_LIST = ollama.list
_DEFAULT_OLLAMA_CHAT = ollama.chat


# ---------------------------------------------------------------------------
# Request / Exception Models
# ---------------------------------------------------------------------------
class STTServiceError(Exception):
    def __init__(self, detail: str, status_code: int = 500, error_code: str = "STT_ERROR") -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.error_code = error_code


class OllamaServiceError(Exception):
    def __init__(
        self,
        detail: str,
        status_code: int = 502,
        error_code: str = "LLM_ERROR",
        ollama_running: Optional[bool] = None,
        requested_model: Optional[str] = None,
        available_models: Optional[List[str]] = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.error_code = error_code
        self.ollama_running = ollama_running
        self.requested_model = OLLAMA_MODEL if requested_model is None else requested_model
        self.available_models = list(available_models) if available_models is not None else []


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)

    @field_validator("username", mode="before")
    @classmethod
    def _strip_username(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class ForgotPasswordRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class VerifyOTPRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    otp: str = Field(min_length=6, max_length=12)


class ResetPasswordOTPRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    otp: str = Field(min_length=6, max_length=12)
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)


class AdminResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)


class AdminUpdateEmailRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)


# ---------------------------------------------------------------------------
# Security Headers
# ---------------------------------------------------------------------------
def _apply_security_headers(response: Response) -> Response:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    content_type = response.headers.get("content-type", "").lower()
    if content_type.startswith("text/html"):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "font-src 'self' https://cdn.jsdelivr.net data:; "
            "script-src 'self' 'unsafe-inline'; "
            "media-src 'self' blob:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "object-src 'none';"
        )
    return response


# ---------------------------------------------------------------------------
# STT (Faster-Whisper)
# ---------------------------------------------------------------------------
def _is_cuda_runtime_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(
        marker in msg
        for marker in ("cublas", "cudnn", "cuda", "nvcuda", "cudart", "libcublas", "libcudnn")
    )


def _run_whisper_transcribe(model: WhisperModel, audio_path: Path) -> str:
    segments, info = model.transcribe(
        str(audio_path),
        language="fa",
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=400),
    )
    transcript = " ".join(segment.text.strip() for segment in segments).strip()
    lang_prob = getattr(info, "language_probability", 0.0)
    logger.info(f"Transcribed Speech ({lang_prob:.2f}): {sanitize_for_log(transcript)}")
    return transcript


def transcribe_persian_audio(audio_path: Path) -> str:
    global whisper_model
    with whisper_lock:
        current_model = whisper_model

    if current_model is None:
        raise STTServiceError(
            detail="مدل تبدیل گفتار به متن (Faster-Whisper) بارگذاری نشده است.",
            status_code=503,
            error_code="STT_MODEL_NOT_INITIALIZED",
        )

    try:
        return _run_whisper_transcribe(current_model, audio_path)
    except Exception as exc:
        if _is_cuda_runtime_error(exc):
            logger.warning(f"Whisper CUDA error ({exc}); switching to CPU (int8)...")
            try:
                with whisper_lock:
                    if whisper_model is current_model or whisper_model is None:
                        whisper_model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
                    retry_model = whisper_model
                return _run_whisper_transcribe(retry_model, audio_path)
            except Exception as retry_exc:
                logger.exception(f"Whisper CPU fallback failed: {retry_exc}")
                raise STTServiceError(
                    detail="خطا در تبدیل گفتار به متن پس از سوییچ به پردازنده مرکزی (CPU).",
                    status_code=500,
                    error_code="STT_TRANSCRIPTION_FAILED",
                ) from retry_exc

        logger.exception(f"Whisper transcription failed: {exc}")
        raise STTServiceError(
            detail="خطا در تبدیل گفتار به متن. لطفاً فرمت فایل صوتی یا لاگ سرور را بررسی کنید.",
            status_code=500,
            error_code="STT_TRANSCRIPTION_FAILED",
        ) from exc


# ---------------------------------------------------------------------------
# LLM Reasoning (Ollama - qwen3.5:4b)
# ---------------------------------------------------------------------------
def _build_system_prompt(default_floor: int = 1) -> str:
    effective_default = 2 if default_floor == 2 else 1
    floor_label_fa = "طبقه دوم" if effective_default == 2 else "طبقه اول"
    return f"""You are an AI IoT Orchestrator for a two-floor smart building.
Your job is to parse Persian voice commands into a strict JSON object for Home Assistant.

### Available Entities Reference (Examples):
- Floor 1 & 2 Lights (`domain`: "light"):
  * `light.living_room` (چراغ پذیرایی / سالن)
  * `light.kitchen` (چراغ آشپزخانه)
  * `light.bedroom` (چراغ اتاق خواب)
  * `light.hallway` (چراغ راهرو)
- Floor 1 & 2 Switches (`domain`: "switch"):
  * `switch.fridge_express_freeze` (انجماد سریع یخچال)
  * `switch.water_heater` (آبگرمکن / پکیج)
  * `switch.coffee_maker` (قهوه‌ساز)
- Floor 1 & 2 Climate (`domain`: "climate"):
  * `climate.main_thermostat` (کولر / سیستم تهویه / ترموستات)

### Output Rules:
1. Respond ONLY with a valid JSON object matching this exact structure:
{{
  "floor": 1, 2, or null (int or null; set to 1 ONLY if Floor 1 / طبقه اول is explicitly mentioned, set to 2 ONLY if Floor 2 / طبقه دوم is explicitly mentioned, otherwise use null or {effective_default}),
  "domain": "light" | "switch" | "climate" | "cover" | "fan",
  "service": "turn_on" | "turn_off" | "set_temperature" | "toggle",
  "entity_id": "<domain>.<object_id>",
  "service_data": {{}} (optional dict, e.g. {{"temperature": 24}} if service is "set_temperature"),
  "ack_message": "Natural Persian immediate acknowledgment (e.g., در حال روشن کردن چراغ پذیرایی {floor_label_fa}...)",
  "success_message": "Natural, polite Persian confirmation to be read aloud after execution (e.g., چراغ پذیرایی در {floor_label_fa} با موفقیت روشن شد.)"
}}
2. Do not wrap the JSON in markdown blocks or add any extra commentary.
"""


def _close_ollama_client(client: Any) -> None:
    close_fn = getattr(client, "close", None)
    if not callable(close_fn):
        close_fn = getattr(getattr(client, "_client", None), "close", None)
    if callable(close_fn):
        try:
            close_fn()
        except Exception:
            pass


def _call_ollama_list() -> Any:
    if ollama.list is not _DEFAULT_OLLAMA_LIST:
        return ollama.list()
    client = ollama.Client(timeout=OLLAMA_LIST_TIMEOUT)
    try:
        return client.list()
    finally:
        _close_ollama_client(client)


def _extract_model_names(list_response: Any) -> List[str]:
    if list_response is None:
        return []
    raw_models = (
        list_response.get("models")
        if isinstance(list_response, dict)
        else getattr(list_response, "models", None)
    )
    if not raw_models:
        return []
    names: List[str] = []
    for item in raw_models:
        if isinstance(item, dict):
            model_name = item.get("model") or item.get("name")
        elif isinstance(item, str):
            model_name = item
        else:
            model_name = getattr(item, "model", None) or getattr(item, "name", None)
        if model_name:
            names.append(str(model_name))
    return names


def _probe_ollama_models() -> List[str]:
    return _extract_model_names(_call_ollama_list())


def _supports_think_kwarg(fn: Any) -> bool:
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        (
            param.name == "think"
            and param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        )
        or param.kind == inspect.Parameter.VAR_KEYWORD
        for param in params
    )


def _call_ollama_chat(**kwargs: Any) -> Any:
    def _invoke(chat_fn: Any) -> Any:
        call_kwargs = dict(kwargs)
        injected_think = False
        if "think" not in call_kwargs and _supports_think_kwarg(chat_fn):
            call_kwargs["think"] = False
            injected_think = True
        try:
            return chat_fn(**call_kwargs)
        except ollama.ResponseError as exc:
            err_text = f"{exc} {getattr(exc, 'error', '')}".lower()
            if injected_think and "think" in err_text:
                call_kwargs.pop("think", None)
                return chat_fn(**call_kwargs)
            raise

    if ollama.chat is not _DEFAULT_OLLAMA_CHAT:
        return _invoke(ollama.chat)
    client = ollama.Client(timeout=OLLAMA_CHAT_TIMEOUT)
    try:
        return _invoke(client.chat)
    finally:
        _close_ollama_client(client)


def _is_ollama_connection_error(exc: Exception) -> bool:
    if isinstance(exc, (ConnectionError, TimeoutError, ollama.RequestError, OSError)):
        return True
    mod = getattr(type(exc), "__module__", "") or ""
    return mod.startswith("httpx")


def _select_ollama_model(requested_model: str, available_models: List[str]) -> Optional[str]:
    if not available_models:
        return None
    if requested_model in available_models:
        return requested_model
    if ":" not in requested_model and f"{requested_model}:latest" in available_models:
        return f"{requested_model}:latest"
    if not OLLAMA_FALLBACK_ENABLED:
        return None
    req_base = requested_model.split(":", 1)[0].strip().lower()
    for candidate in available_models:
        if candidate.split(":", 1)[0].strip().lower() == req_base:
            return candidate
    for candidate in available_models:
        if candidate.split(":", 1)[0].strip().lower().startswith("qwen"):
            return candidate
    return available_models[0]


def _extract_chat_content(response: Any) -> str:
    if isinstance(response, dict):
        message = response.get("message")
        if isinstance(message, dict):
            return str(message.get("content") or "")
        return str(getattr(message, "content", "") or "")
    message = getattr(response, "message", None)
    if message is not None:
        content = getattr(message, "content", None)
        if content is not None:
            return str(content)
    return ""


def _normalize_floor(raw_floor: Any, default_floor: int = 1) -> int:
    effective_default = 2 if default_floor == 2 else 1
    if raw_floor is None:
        return effective_default
    if isinstance(raw_floor, bool):
        raise ValueError("Boolean value is not valid for floor.")
    if isinstance(raw_floor, int):
        if raw_floor in (1, 2):
            return raw_floor
        raise ValueError(f"Unsupported integer floor value: {raw_floor}")
    if isinstance(raw_floor, float):
        if math.isfinite(raw_floor) and raw_floor in (1.0, 2.0):
            return int(raw_floor)
        raise ValueError(f"Unsupported float floor value: {raw_floor}")
    if isinstance(raw_floor, str):
        digit_map = str.maketrans("۱۲١٢", "1212")
        stripped = raw_floor.strip().translate(digit_map).lower()
        if stripped in ("", "null", "none"):
            return effective_default
        if stripped in ("1", "floor 1", "floor1", "طبقه 1", "طبقه اول", "طبقه یک"):
            return 1
        if stripped in ("2", "floor 2", "floor2", "طبقه 2", "طبقه دوم", "طبقه دو"):
            return 2
        raise ValueError(f"Unrecognized floor value: {sanitize_for_log(raw_floor, 50)}")
    raise ValueError("Unsupported floor type.")


def _is_unspecified_floor(raw_floor: Any) -> bool:
    return raw_floor is None or (
        isinstance(raw_floor, str) and raw_floor.strip().lower() in ("", "null", "none")
    )


def _select_preferred_command_dict(candidates: List[Dict[str, Any]], default_floor: int = 1) -> Dict[str, Any]:
    command_keys = {"domain", "service", "entity_id"}
    valid_candidates: List[Tuple[Dict[str, Any], Optional[int]]] = []
    for candidate in candidates:
        try:
            probe = dict(candidate)
            raw_floor = probe.get("floor")
            unspecified = _is_unspecified_floor(raw_floor)
            normalized_floor = _normalize_floor(raw_floor, default_floor=default_floor)
            probe["floor"] = normalized_floor
            LLMCommandSchema(**probe)
            valid_candidates.append((candidate, None if unspecified else normalized_floor))
        except (ValidationError, TypeError, ValueError):
            continue

    if valid_candidates:
        valid_explicit_floors = {fl for _, fl in valid_candidates if fl is not None}
        valid_has_unspecified = any(fl is None for _, fl in valid_candidates)
        if len(valid_explicit_floors) > 1 or (valid_explicit_floors and valid_has_unspecified):
            raise ValueError("Conflicting floor values in multi-JSON LLM output.")
        return valid_candidates[0][0]

    for candidate in candidates:
        if command_keys.issubset(candidate.keys()):
            return candidate
    return candidates[0]


def extract_json_from_llm(raw_content: str, default_floor: int = 1) -> Dict[str, Any]:
    if not isinstance(raw_content, str) or not raw_content.strip():
        raise ValueError("Empty LLM response content.")

    cleaned = re.sub(r"<think>.*?</think>", "", raw_content, flags=re.DOTALL | re.IGNORECASE).strip()
    if "</think>" in cleaned.lower():
        cleaned = re.split(r"</think>", cleaned, flags=re.IGNORECASE)[-1].strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    fence_pattern = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)
    fence_candidates: List[Dict[str, Any]] = []
    for match in fence_pattern.finditer(cleaned):
        try:
            parsed = json.loads(match.group(1).strip())
            if isinstance(parsed, dict):
                fence_candidates.append(parsed)
        except json.JSONDecodeError:
            pass
    if fence_candidates:
        return _select_preferred_command_dict(fence_candidates, default_floor=default_floor)

    decoder = json.JSONDecoder()
    scan_candidates: List[Dict[str, Any]] = []
    idx = 0
    while idx < len(cleaned):
        brace_pos = cleaned.find("{", idx)
        if brace_pos == -1:
            break
        try:
            parsed, _ = decoder.raw_decode(cleaned, brace_pos)
            if isinstance(parsed, dict):
                scan_candidates.append(parsed)
        except json.JSONDecodeError:
            pass
        idx = brace_pos + 1

    if scan_candidates:
        return _select_preferred_command_dict(scan_candidates, default_floor=default_floor)

    raise ValueError("No valid JSON object found in LLM output.")


def reason_with_qwen(transcript: str, default_floor: int = 1) -> LLMCommandSchema:
    effective_default = 2 if default_floor == 2 else 1
    system_prompt = _build_system_prompt(default_floor=effective_default)

    try:
        list_response = _call_ollama_list()
    except Exception as exc:
        logger.exception(f"Ollama reachability check failed: {exc}")
        raise OllamaServiceError(
            detail="سرویس Ollama در دسترس نیست یا در حال اجرا نمی‌باشد. لطفاً اجرای سرویس Ollama را بررسی کنید.",
            status_code=503,
            error_code="OLLAMA_UNREACHABLE",
            ollama_running=False,
            requested_model=OLLAMA_MODEL,
            available_models=[],
        ) from exc

    available_models = _extract_model_names(list_response)
    active_model = _select_ollama_model(OLLAMA_MODEL, available_models)
    if active_model is None:
        models_list_text = "، ".join(available_models) if available_models else "هیچ مدلی نصب نیست"
        raise OllamaServiceError(
            detail=f"مدل «{OLLAMA_MODEL}» در سرویس Ollama یافت نشد. مدل‌های موجود: {models_list_text}.",
            status_code=404,
            error_code="MODEL_NOT_FOUND",
            ollama_running=True,
            requested_model=OLLAMA_MODEL,
            available_models=available_models,
        )

    logger.info(f"Querying Ollama model '{active_model}' (default_floor={effective_default})...")
    try:
        response = _call_ollama_chat(
            model=active_model,
            format="json",
            options={"temperature": 0.1},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": transcript},
            ],
        )
    except ollama.ResponseError as exc:
        resp_status = getattr(exc, "status_code", None)
        if resp_status == 404:
            raise OllamaServiceError(
                detail=f"مدل «{active_model}» در سرویس Ollama یافت نشد.",
                status_code=404,
                error_code="MODEL_NOT_FOUND",
                ollama_running=True,
                requested_model=active_model,
                available_models=available_models,
            ) from exc
        raise OllamaServiceError(
            detail=f"خطا در دریافت پاسخ از مدل «{active_model}» در سرویس Ollama.",
            status_code=502,
            error_code="OLLAMA_CHAT_ERROR",
            ollama_running=True,
            requested_model=active_model,
            available_models=available_models,
        ) from exc
    except Exception as exc:
        if _is_ollama_connection_error(exc):
            raise OllamaServiceError(
                detail="ارتباط با سرویس Ollama حین پردازش فرمان قطع شد.",
                status_code=503,
                error_code="OLLAMA_UNREACHABLE",
                ollama_running=False,
                requested_model=active_model,
                available_models=available_models,
            ) from exc
        raise OllamaServiceError(
            detail=f"خطای غیرمنتظره در فراخوانی مدل Ollama ({active_model}).",
            status_code=502,
            error_code="OLLAMA_CHAT_ERROR",
            ollama_running=True,
            requested_model=active_model,
            available_models=available_models,
        ) from exc

    raw_content = _extract_chat_content(response)
    logger.info(f"Ollama JSON Output ({active_model}): {sanitize_for_log(raw_content)}")

    try:
        parsed_dict = extract_json_from_llm(raw_content, default_floor=effective_default)
        parsed_dict["floor"] = _normalize_floor(parsed_dict.get("floor"), default_floor=effective_default)
        command = LLMCommandSchema(**parsed_dict)
        command._model_used = active_model
        return command
    except (ValueError, json.JSONDecodeError, ValidationError, TypeError) as parse_exc:
        logger.exception(f"Failed to parse LLM JSON output ({active_model}): {parse_exc}")
        raise OllamaServiceError(
            detail=f"خروجی مدل زبانی ({active_model}) ساختار معتبر JSON فرمان خانه هوشمند نداشت.",
            status_code=502,
            error_code="LLM_INVALID_JSON",
            ollama_running=True,
            requested_model=active_model,
            available_models=available_models,
        ) from parse_exc


# ---------------------------------------------------------------------------
# Hybrid TTS (Edge-TTS Primary + Local Piper TTS Fallback + Disk Pre-Cache)
# ---------------------------------------------------------------------------
def _find_piper_model_files() -> Optional[Tuple[Path, Path]]:
    """Finds local Piper ONNX model and JSON config under PIPER_MODELS_DIR."""
    search_dirs = [PIPER_MODELS_DIR, BASE_DIR / "storage" / "models", BASE_DIR]
    for folder in search_dirs:
        if not folder.exists():
            continue
        exact_onnx = folder / f"{PIPER_MODEL_NAME}.onnx"
        exact_json = folder / f"{PIPER_MODEL_NAME}.onnx.json"
        if exact_onnx.exists() and exact_json.exists():
            return exact_onnx, exact_json
        # Also match any fa_IR*.onnx if user placed another Persian Piper model
        for onnx_file in sorted(folder.glob("fa_IR*.onnx")):
            json_file = Path(f"{onnx_file}.json")
            if json_file.exists():
                return onnx_file, json_file
    return None


def _get_piper_voice() -> Optional[Any]:
    global piper_voice, _piper_load_attempted
    if PiperVoice is None:
        return None
    with piper_lock:
        if piper_voice is not None:
            return piper_voice
        if _piper_load_attempted:
            return None
        _piper_load_attempted = True
        model_files = _find_piper_model_files()
        if model_files is None:
            logger.warning(
                f"Local Piper model '{PIPER_MODEL_NAME}' not found in '{PIPER_MODELS_DIR}'. "
                "Offline Piper fallback will remain standby until .onnx + .onnx.json files are present."
            )
            return None
        onnx_path, json_path = model_files
        try:
            logger.info(f"Loading local Piper TTS voice '{onnx_path.name}'...")
            piper_voice = PiperVoice.load(onnx_path, config_path=json_path, use_cuda=False)
            logger.info("Local Piper TTS voice loaded successfully.")
            return piper_voice
        except Exception as exc:
            logger.exception(f"Failed to load Piper voice '{onnx_path.name}': {exc}")
            return None


def _synthesize_with_piper_sync(text: str, wav_output_path: Path) -> bool:
    voice = _get_piper_voice()
    if voice is None:
        return False
    wav_output_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(wav_output_path), "wb") as wav_file:
        voice.synthesize_wav(text, wav_file)
    return wav_output_path.exists() and wav_output_path.stat().st_size > 44


def _get_cached_phrase_path(text: str) -> Optional[Path]:
    """Returns a cached .mp3 or .wav file from TTS_CACHE_DIR if text matches a pre-cached phrase."""
    text_hash = hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:24]
    for ext in (".mp3", ".wav"):
        candidate = TTS_CACHE_DIR / f"err_{text_hash}{ext}"
        if candidate.exists() and candidate.stat().st_size > 44:
            return candidate
    return None


def _register_audio_owner(response_id: str, username: str) -> None:
    id_norm = response_id.lower()
    with _audio_owner_lock:
        _AUDIO_OWNER_BY_ID[id_norm] = username
        _AUDIO_OWNER_BY_ID.move_to_end(id_norm)
        while len(_AUDIO_OWNER_BY_ID) > MAX_AUDIO_OWNER_ENTRIES:
            _AUDIO_OWNER_BY_ID.popitem(last=False)


def _get_audio_owner(response_id: str) -> Optional[str]:
    with _audio_owner_lock:
        return _AUDIO_OWNER_BY_ID.get(response_id.lower())


def _get_user_latest_audio_paths(username: str) -> Tuple[Path, Path]:
    safe_user = re.sub(r"[^a-zA-Z0-9_-]", "_", username or "unknown")
    return TEMP_AUDIO_DIR / f"latest_response_{safe_user}.mp3", TEMP_AUDIO_DIR / f"latest_response_{safe_user}.wav"


def _update_latest_audio_atomically(source_path: Path, target_path: Path) -> None:
    _ensure_temp_audio_dir(target_path.parent)
    fd, tmp_name = tempfile.mkstemp(prefix=".latest_", suffix=f"{source_path.suffix}.tmp", dir=target_path.parent)
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        shutil.copyfile(source_path, tmp_path)
        os.replace(tmp_path, target_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _prune_old_tts_files(keep_count: int = MAX_CACHED_TTS_FILES) -> None:
    if not TEMP_AUDIO_DIR.exists():
        return
    limit = max(0, keep_count)
    files_with_mtime: List[Tuple[int, Path]] = []
    for pattern in ("response_*.mp3", "response_*.wav"):
        for path in TEMP_AUDIO_DIR.glob(pattern):
            try:
                files_with_mtime.append((path.stat().st_mtime_ns, path))
            except OSError:
                continue
    files_with_mtime.sort(key=lambda item: item[0], reverse=True)
    removed_ids: Set[str] = set()
    for _, stale_file in files_with_mtime[limit:]:
        try:
            stale_file.unlink(missing_ok=True)
            removed_ids.add(stale_file.stem.removeprefix("response_").lower())
        except OSError:
            pass
    with _audio_owner_lock:
        for stale_id in removed_ids:
            _AUDIO_OWNER_BY_ID.pop(stale_id, None)


def _format_tts_error_label(exc: Exception) -> str:
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "Edge-TTS timeout"
    return f"TTS error ({type(exc).__name__})"


async def synthesize_persian_tts(
    text: str,
    output_stem_path: Path,
    allow_cache_hit: bool = True,
) -> Dict[str, Any]:
    """
    Hybrid Persian TTS:
    1. Instant disk cache hit for pre-cached common phrases.
    2. Primary: Edge-TTS -> .mp3
    3. Zero-crash offline fallback: Local Piper TTS (fa_IR-amir-medium) -> .wav
    """
    cleaned_text = (text or "").strip()
    if not cleaned_text:
        return {
            "ok": False,
            "engine": "none",
            "voice": TTS_VOICE,
            "path": None,
            "fallback_message": TTS_UNAVAILABLE_MESSAGE,
            "error": "Empty TTS input text",
        }

    # 1. Check pre-cached disk audio
    if allow_cache_hit:
        cached = _get_cached_phrase_path(cleaned_text)
        if cached is not None:
            dest_path = output_stem_path.with_suffix(cached.suffix)
            await asyncio.to_thread(shutil.copyfile, cached, dest_path)
            return {
                "ok": True,
                "engine": "disk-cache",
                "voice": f"cached:{cached.suffix.lstrip('.')}",
                "path": dest_path,
                "fallback_message": None,
                "error": None,
            }

    # 2. Primary: Edge-TTS (.mp3)
    mp3_path = output_stem_path.with_suffix(".mp3")
    edge_error_label: Optional[str] = None
    try:
        communicate = edge_tts.Communicate(text=cleaned_text, voice=TTS_VOICE)
        await asyncio.wait_for(communicate.save(str(mp3_path)), timeout=TTS_TIMEOUT_SECONDS)
        if mp3_path.exists() and mp3_path.stat().st_size > 0:
            return {
                "ok": True,
                "engine": "edge-tts",
                "voice": TTS_VOICE,
                "path": mp3_path,
                "fallback_message": None,
                "error": None,
            }
        raise RuntimeError("Edge-TTS produced an empty file.")
    except Exception as edge_exc:
        edge_error_label = _format_tts_error_label(edge_exc)
        logger.warning(f"Edge-TTS failed ({edge_exc}); engaging local Piper TTS fallback...")
        try:
            mp3_path.unlink(missing_ok=True)
        except OSError:
            pass

    # 3. Offline Fallback: Local Piper TTS (.wav)
    wav_path = output_stem_path.with_suffix(".wav")
    try:
        piper_ok = await asyncio.to_thread(_synthesize_with_piper_sync, cleaned_text, wav_path)
        if piper_ok and wav_path.exists() and wav_path.stat().st_size > 44:
            return {
                "ok": True,
                "engine": "piper-offline",
                "voice": PIPER_MODEL_NAME,
                "path": wav_path,
                "fallback_message": None,
                "error": None,
            }
    except Exception as piper_exc:
        logger.exception(f"Piper offline TTS fallback failed: {piper_exc}")
        try:
            wav_path.unlink(missing_ok=True)
        except OSError:
            pass

    return {
        "ok": False,
        "engine": "none",
        "voice": PIPER_MODEL_NAME,
        "path": None,
        "fallback_message": TTS_UNAVAILABLE_MESSAGE,
        "error": edge_error_label or "Piper voice unavailable",
    }


async def _prepare_user_audio_response(text: str, username: str) -> Tuple[Optional[str], Dict[str, Any]]:
    """Synthesizes (or retrieves cached) TTS audio and registers owner-isolated /play-response?id=..."""
    response_id = secrets.token_hex(16)
    output_stem = TEMP_AUDIO_DIR / f"response_{response_id}"
    _ensure_temp_audio_dir()
    tts_result = await synthesize_persian_tts(text, output_stem)
    out_path: Optional[Path] = tts_result.get("path")
    if tts_result.get("ok") and isinstance(out_path, Path) and out_path.exists() and out_path.stat().st_size > 0:
        _register_audio_owner(response_id, username)
        mp3_latest, wav_latest = _get_user_latest_audio_paths(username)
        target_latest = wav_latest if out_path.suffix.lower() == ".wav" else mp3_latest
        # Remove stale opposite-format latest file so replay always gets the newest response
        opposite_latest = mp3_latest if out_path.suffix.lower() == ".wav" else wav_latest
        try:
            opposite_latest.unlink(missing_ok=True)
        except OSError:
            pass
        await asyncio.to_thread(_update_latest_audio_atomically, out_path, target_latest)
        await asyncio.to_thread(_prune_old_tts_files)
        public_status = {k: v for k, v in tts_result.items() if k != "path"}
        return f"/play-response?id={response_id}", public_status

    public_status = {k: v for k, v in tts_result.items() if k != "path"}
    return None, public_status


async def _prewarm_common_error_tts_cache() -> None:
    """Pre-caches common Persian error responses on disk (preferring Piper offline, or Edge-TTS)."""
    TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for key, phrase in COMMON_ERROR_TTS_PHRASES.items():
        if _get_cached_phrase_path(phrase) is not None:
            continue
        text_hash = hashlib.sha256(phrase.strip().encode("utf-8")).hexdigest()[:24]
        stem = TTS_CACHE_DIR / f"err_{text_hash}"
        # Prefer local Piper for instant zero-network pre-caching if available; otherwise use hybrid
        wav_candidate = stem.with_suffix(".wav")
        try:
            piper_ok = await asyncio.to_thread(_synthesize_with_piper_sync, phrase, wav_candidate)
            if piper_ok:
                logger.info(f"Pre-cached error TTS '{key}' via local Piper -> {wav_candidate.name}")
                continue
        except Exception:
            pass
        res = await synthesize_persian_tts(phrase, stem, allow_cache_hit=False)
        if res.get("ok"):
            logger.info(f"Pre-cached error TTS '{key}' via {res.get('engine')}.")


# ---------------------------------------------------------------------------
# FastAPI App & Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    global whisper_model
    ensure_users_file()
    validate_startup_auth_config()

    logger.info(f"Loading Faster-Whisper model ('{WHISPER_MODEL_SIZE}')...")
    try:
        whisper_model = await asyncio.to_thread(
            lambda: WhisperModel(WHISPER_MODEL_SIZE, device="auto", compute_type="auto")
        )
        logger.info("Faster-Whisper loaded successfully.")
    except Exception as exc:
        logger.warning(f"Auto device Whisper init failed ({exc}); falling back to CPU (int8)...")
        try:
            whisper_model = await asyncio.to_thread(
                lambda: WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
            )
            logger.info("Faster-Whisper loaded on CPU.")
        except Exception as cpu_err:
            logger.exception(f"Faster-Whisper CPU initialization failed: {cpu_err}")
            whisper_model = None

    # Preload Piper model & pre-cache common error responses in background
    await asyncio.to_thread(_get_piper_voice)
    asyncio.create_task(_prewarm_common_error_tts_cache())

    yield

    shutil.rmtree(TEMP_AUDIO_DIR, ignore_errors=True)
    logger.info("Cleaned up temporary audio directory.")


app = FastAPI(
    title="Smart Building Local Voice Control Gateway",
    version="2.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Security Middleware & Global Exception Handlers
# ---------------------------------------------------------------------------
def _parse_content_length(request: Request) -> Optional[int]:
    raw = request.headers.get("content-length")
    if raw is None:
        return None
    stripped = raw.strip()
    if not stripped or not stripped.isdigit():
        return -1
    return int(stripped)


@app.middleware("http")
async def security_and_error_middleware(request: Request, call_next):
    try:
        normalized_path = request.url.path.rstrip("/") or "/"
        is_public = request.method == "OPTIONS" or (request.method, normalized_path) in {
            ("GET", "/"),
            ("HEAD", "/"),
            ("POST", "/api/login"),
            ("POST", "/api/auth/forgot-password"),
            ("POST", "/api/auth/verify-otp"),
            ("POST", "/api/auth/reset-password"),
        }

        if not is_public:
            if normalized_path == "/process-audio":
                content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if "authorization" in request.headers:
                    auth_error = check_bearer_auth(request)
                    if auth_error is not None:
                        return _apply_security_headers(auth_error)
                    content_length = _parse_content_length(request)
                    if content_length is not None and content_length > (MAX_AUDIO_UPLOAD_BYTES + 131072):
                        return _apply_security_headers(
                            build_error_response(
                                status_code=413,
                                detail="حجم فایل صوتی ارسالی بیشتر از حد مجاز است.",
                                stage="upload",
                                error_code="PAYLOAD_TOO_LARGE",
                            )
                        )
                elif request.method == "POST" and content_type in (
                    "multipart/form-data",
                    "application/x-www-form-urlencoded",
                ):
                    request.state.auth_user = None
                else:
                    return _apply_security_headers(
                        build_error_response(
                            status_code=401,
                            detail="احراز هویت نامعتبر است یا توکن دسترسی ارسال نشده است.",
                            stage="auth",
                            error_code="UNAUTHORIZED",
                            headers=NO_STORE_HEADERS,
                        )
                    )
            else:
                auth_error = check_bearer_auth(request)
                if auth_error is not None:
                    return _apply_security_headers(auth_error)

        response = await call_next(request)
    except Exception as exc:
        logger.exception(f"Unhandled middleware error on {request.method} {sanitize_for_log(request.url.path)}: {exc}")
        response = build_error_response(
            status_code=500,
            detail="خطای غیرمنتظره در سرور رخ داد. لطفاً لاگ‌های سرور را بررسی کنید.",
            stage="internal",
            error_code="INTERNAL_SERVER_ERROR",
        )
    return _apply_security_headers(response)


app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.exception_handler(GatewayAuthError)
async def gateway_auth_exception_handler(request: Request, exc: GatewayAuthError) -> JSONResponse:
    return build_error_response(
        status_code=exc.status_code,
        detail=exc.detail,
        stage=exc.stage,
        error_code=exc.error_code,
        headers=exc.headers or NO_STORE_HEADERS,
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail
    stage = "internal"
    error_code = f"HTTP_{exc.status_code}"
    if isinstance(detail, dict):
        detail_msg = str(detail.get("detail") or detail)
        stage = str(detail.get("stage") or stage)
        error_code = str(detail.get("error_code") or error_code)
    else:
        detail_msg = str(detail)
    headers = {**NO_STORE_HEADERS, **(getattr(exc, "headers", None) or {})} if exc.status_code in (401, 403, 429) else None
    return build_error_response(
        status_code=exc.status_code,
        detail=detail_msg,
        stage=stage,
        error_code=error_code,
        headers=headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return build_error_response(
        status_code=422,
        detail="درخواست ارسالی معتبر نیست. لطفاً ورودی‌های فرم را بررسی کنید.",
        stage="auth" if "/api/" in request.url.path else "upload",
        error_code="VALIDATION_ERROR",
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(f"Unhandled exception: {exc}")
    return build_error_response(
        status_code=500,
        detail="خطای غیرمنتظره در سرور رخ داد.",
        stage="internal",
        error_code="INTERNAL_SERVER_ERROR",
    )


# ---------------------------------------------------------------------------
# Public & Auth Endpoints
# ---------------------------------------------------------------------------
@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def serve_pwa_client():
    index_file = BASE_DIR / "www" / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h3>index.html not found under www/ directory</h3>", status_code=404)
    html_content = await asyncio.to_thread(index_file.read_text, encoding="utf-8")
    return HTMLResponse(content=html_content)


@app.post("/api/login")
async def login(request: Request, payload: LoginRequest):
    client_ip = request.client.host if request.client and request.client.host else "unknown"
    retry_after = check_login_rate_limit(client_ip)
    if retry_after is not None:
        return build_error_response(
            status_code=429,
            detail="تعداد تلاش‌های ناموفق ورود بیش از حد مجاز است. لطفاً کمی بعد دوباره تلاش کنید.",
            stage="auth",
            error_code="RATE_LIMITED",
            headers={**NO_STORE_HEADERS, "Retry-After": str(retry_after)},
        )

    registry = await asyncio.to_thread(get_user_registry)
    user_record = registry.get(payload.username)
    stored_hash = user_record.password_hash if (user_record is not None and user_record.active) else None
    verified = await asyncio.to_thread(verify_password_pbkdf2, payload.password, stored_hash)

    if user_record is None or not user_record.active or not verified:
        record_login_failure(client_ip)
        return build_error_response(
            status_code=401,
            detail="نام کاربری یا رمز عبور نامعتبر است.",
            stage="auth",
            error_code="INVALID_CREDENTIALS",
            headers=NO_STORE_HEADERS,
        )

    clear_login_failures(client_ip)
    token, expires_in = create_access_token(user_record)
    logger.info(f"Authenticated user='{sanitize_for_log(user_record.username, 64)}' role='{user_record.role}'")
    return JSONResponse(
        status_code=200,
        content={
            "authenticated": True,
            "token": token,
            "token_type": "Bearer",
            "expires_in": expires_in,
            "username": user_record.username,
            "email": user_record.email,
            "role": user_record.role,
            "floor": user_record.floor,
            "allowed_floors": list(user_record.allowed_floors),
        },
        headers=NO_STORE_HEADERS,
    )


@app.post("/api/auth/forgot-password")
async def forgot_password(request: Request, payload: ForgotPasswordRequest):
    client_ip = request.client.host if request.client and request.client.host else "unknown"
    retry_after = check_login_rate_limit(f"otp:{client_ip}")
    if retry_after is not None:
        return build_error_response(
            status_code=429,
            detail="تعداد درخواست‌های بازیابی رمز بیش از حد مجاز است. کمی بعد تلاش کنید.",
            stage="auth",
            error_code="RATE_LIMITED",
            headers={**NO_STORE_HEADERS, "Retry-After": str(retry_after)},
        )
    record_login_failure(f"otp:{client_ip}")
    result = await asyncio.to_thread(request_password_reset_otp, payload.email)
    return JSONResponse(status_code=200, content=result, headers=NO_STORE_HEADERS)


@app.post("/api/auth/verify-otp")
async def verify_otp(payload: VerifyOTPRequest):
    result = await asyncio.to_thread(verify_password_reset_otp, payload.email, payload.otp)
    return JSONResponse(status_code=200, content=result, headers=NO_STORE_HEADERS)


@app.post("/api/auth/reset-password")
async def reset_password(payload: ResetPasswordOTPRequest):
    result = await asyncio.to_thread(
        reset_password_with_otp, payload.email, payload.otp, payload.new_password
    )
    return JSONResponse(status_code=200, content=result, headers=NO_STORE_HEADERS)


@app.get("/api/me")
async def get_current_user_profile(auth_user: AuthUser = Depends(require_authenticated_user)):
    return JSONResponse(
        status_code=200,
        content={
            "authenticated": True,
            "username": auth_user.username,
            "email": auth_user.email,
            "role": auth_user.role,
            "floor": auth_user.floor,
            "allowed_floors": list(auth_user.allowed_floors),
            "expires_at": auth_user.expires_at,
        },
        headers=NO_STORE_HEADERS,
    )


# ---------------------------------------------------------------------------
# Admin Panel APIs (User Manager + Live Hardware & Network Telemetry)
# ---------------------------------------------------------------------------
@app.get("/api/admin/system-stats")
async def admin_system_stats(admin_user: AuthUser = Depends(require_admin_user)):
    with whisper_lock:
        whisper_ready = whisper_model is not None
    stats = await asyncio.to_thread(
        collect_admin_system_stats,
        whisper_ready=whisper_ready,
        whisper_model_size=WHISPER_MODEL_SIZE,
        ollama_model=OLLAMA_MODEL,
        ollama_probe_fn=_probe_ollama_models,
    )
    return JSONResponse(status_code=200, content=stats, headers=NO_STORE_HEADERS)


@app.get("/api/admin/users")
async def admin_list_users(admin_user: AuthUser = Depends(require_admin_user)):
    users = await asyncio.to_thread(list_users_public)
    return JSONResponse(status_code=200, content={"users": users}, headers=NO_STORE_HEADERS)


@app.post("/api/admin/users/{username}/reset-password")
async def admin_reset_user_password(
    username: str,
    payload: AdminResetPasswordRequest,
    admin_user: AuthUser = Depends(require_admin_user),
):
    try:
        updated = await asyncio.to_thread(update_user_password, username, payload.new_password)
    except KeyError:
        return build_error_response(
            status_code=404,
            detail="کاربر مورد نظر یافت نشد.",
            stage="auth",
            error_code="USER_NOT_FOUND",
            headers=NO_STORE_HEADERS,
        )
    except ValueError as val_err:
        return build_error_response(
            status_code=422,
            detail=str(val_err),
            stage="auth",
            error_code="VALIDATION_ERROR",
            headers=NO_STORE_HEADERS,
        )
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "username": updated.username,
            "message": f"رمز عبور کاربر «{updated.username}» با موفقیت بازنشانی شد.",
        },
        headers=NO_STORE_HEADERS,
    )


@app.post("/api/admin/users/{username}/email")
async def admin_update_user_email(
    username: str,
    payload: AdminUpdateEmailRequest,
    admin_user: AuthUser = Depends(require_admin_user),
):
    try:
        updated = await asyncio.to_thread(update_user_email, username, payload.email)
    except KeyError:
        return build_error_response(
            status_code=404,
            detail="کاربر مورد نظر یافت نشد.",
            stage="auth",
            error_code="USER_NOT_FOUND",
            headers=NO_STORE_HEADERS,
        )
    except ValueError as val_err:
        return build_error_response(
            status_code=422,
            detail=str(val_err),
            stage="auth",
            error_code="VALIDATION_ERROR",
            headers=NO_STORE_HEADERS,
        )
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "username": updated.username,
            "email": updated.email,
            "message": f"ایمیل کاربر «{updated.username}» با موفقیت ذخیره شد.",
        },
        headers=NO_STORE_HEADERS,
    )


# ---------------------------------------------------------------------------
# Voice Processing Pipeline (/process-audio & /play-response)
# ---------------------------------------------------------------------------
class _RequestSizeLimitExceeded(Exception):
    pass


def _is_upload_file_obj(obj: Any) -> bool:
    if obj is None or isinstance(obj, (str, bytes)):
        return False
    return isinstance(obj, StarletteUploadFile) or (
        callable(getattr(obj, "read", None)) and hasattr(obj, "filename")
    )


@app.post("/process-audio")
@app.post("/process-audio/", include_in_schema=False)
async def process_audio(request: Request):
    temp_input_path: Optional[Path] = None
    form_data: Any = None
    audio_obj: Any = None
    current_stage = "auth"

    try:
        auth_user: Optional[AuthUser] = getattr(request.state, "auth_user", None)
        if not isinstance(auth_user, AuthUser) and "authorization" in request.headers:
            auth_user = authenticate_bearer_header(request.headers.get("Authorization", ""))
            request.state.auth_user = auth_user

        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        is_form_content = content_type in ("multipart/form-data", "application/x-www-form-urlencoded")
        if not isinstance(auth_user, AuthUser) and not is_form_content:
            raise GatewayAuthError(
                status_code=401,
                detail="احراز هویت نامعتبر است یا توکن دسترسی ارسال نشده است.",
                error_code="UNAUTHORIZED",
                headers=NO_STORE_HEADERS,
            )

        if is_form_content:
            max_body_bytes = MAX_AUDIO_UPLOAD_BYTES + 131072
            orig_receive = request._receive
            received_bytes = 0

            async def _bounded_receive():
                nonlocal received_bytes
                message = await orig_receive()
                if message.get("type") == "http.request":
                    received_bytes += len(message.get("body", b""))
                    if received_bytes > max_body_bytes:
                        raise _RequestSizeLimitExceeded()
                return message

            request._receive = _bounded_receive
            try:
                form_data = await request.form(
                    max_files=1,
                    max_fields=6,
                    max_part_size=max(MAX_AUDIO_UPLOAD_BYTES, 4096),
                )
            except _RequestSizeLimitExceeded:
                return build_error_response(
                    status_code=413,
                    detail="حجم درخواست ارسالی بیش از حد مجاز است.",
                    stage="upload",
                    error_code="PAYLOAD_TOO_LARGE",
                )
            finally:
                request._receive = orig_receive

        if not isinstance(auth_user, AuthUser):
            try:
                from app.auth import verify_access_token
            except ImportError:
                try:
                    from auth import verify_access_token
                except ImportError:
                    from .auth import verify_access_token

            token_entries = form_data.getlist("token") if form_data is not None else []
            if len(token_entries) != 1 or not isinstance(token_entries[0], str) or not token_entries[0].strip():
                raise GatewayAuthError(
                    status_code=401,
                    detail="احراز هویت نامعتبر است یا توکن دسترسی ارسال نشده است.",
                    error_code="UNAUTHORIZED",
                    headers=NO_STORE_HEADERS,
                )
            auth_user = verify_access_token(token_entries[0].strip())
            request.state.auth_user = auth_user

        override_err = check_client_floor_override(request, auth_user, form_data)
        if override_err is not None:
            return override_err

        current_stage = "upload"
        audio_entries = form_data.getlist("audio") if form_data is not None else []
        audio_obj = audio_entries[0] if len(audio_entries) == 1 else None
        if not _is_upload_file_obj(audio_obj):
            return build_error_response(
                status_code=422,
                detail="فایل صوتی ارسال نشده است یا فرمت فیلد نامعتبر است.",
                stage="upload",
                error_code="VALIDATION_ERROR",
            )

        first_chunk = await audio_obj.read(65536)
        if not first_chunk:
            return build_error_response(
                status_code=400,
                detail="فایل صوتی ارسالی خالی است.",
                stage="upload",
                error_code="EMPTY_AUDIO_FILE",
            )

        raw_suffix = Path(audio_obj.filename or "recording.webm").suffix.lower()
        suffix = raw_suffix if raw_suffix in ALLOWED_AUDIO_SUFFIXES else ".webm"
        _ensure_temp_audio_dir()
        fd, temp_input_path_str = tempfile.mkstemp(suffix=suffix, dir=TEMP_AUDIO_DIR)
        temp_input_path = Path(temp_input_path_str)
        total_bytes = 0
        with os.fdopen(fd, "wb") as tmp_file:
            chunk = first_chunk
            while chunk:
                total_bytes += len(chunk)
                if total_bytes > MAX_AUDIO_UPLOAD_BYTES:
                    return build_error_response(
                        status_code=413,
                        detail="حجم فایل صوتی ارسالی بیشتر از حد مجاز است.",
                        stage="upload",
                        error_code="PAYLOAD_TOO_LARGE",
                    )
                await asyncio.to_thread(tmp_file.write, chunk)
                chunk = await audio_obj.read(65536)

        # 1. STT via Faster-Whisper
        current_stage = "stt"
        try:
            transcript = await asyncio.to_thread(transcribe_persian_audio, temp_input_path)
        except STTServiceError as stt_err:
            err_audio_url, _ = await _prepare_user_audio_response(stt_err.detail, auth_user.username)
            return build_error_response(
                status_code=stt_err.status_code,
                detail=stt_err.detail,
                stage="stt",
                error_code=stt_err.error_code,
                audio_url=err_audio_url,
            )

        if not transcript:
            msg = COMMON_ERROR_TTS_PHRASES["empty_transcript"]
            err_audio_url, _ = await _prepare_user_audio_response(msg, auth_user.username)
            return build_error_response(
                status_code=422,
                detail=msg,
                stage="stt",
                error_code="EMPTY_TRANSCRIPT",
                audio_url=err_audio_url,
            )

        # 2. Pre-LLM Floor Guard
        current_stage = "auth"
        explicit_floors = detect_explicit_floors(transcript)
        disallowed_floors = explicit_floors - set(auth_user.allowed_floors)
        if disallowed_floors:
            detail_msg = format_floor_forbidden_detail(disallowed_floors, auth_user.allowed_floors)
            err_audio_url, _ = await _prepare_user_audio_response(
                COMMON_ERROR_TTS_PHRASES["floor_forbidden"], auth_user.username
            )
            return build_error_response(
                status_code=403,
                detail=detail_msg,
                stage="auth",
                error_code="FLOOR_FORBIDDEN",
                audio_url=err_audio_url,
                headers=NO_STORE_HEADERS,
            )

        # 3. Ollama Reasoning (qwen3.5:4b)
        current_stage = "llm"
        try:
            command_data = await asyncio.to_thread(reason_with_qwen, transcript, auth_user.default_floor)
        except OllamaServiceError as llm_err:
            err_audio_url, _ = await _prepare_user_audio_response(
                COMMON_ERROR_TTS_PHRASES["ollama_offline"], auth_user.username
            )
            return build_error_response(
                status_code=llm_err.status_code,
                detail=llm_err.detail,
                stage="llm",
                error_code=llm_err.error_code,
                ollama_running=llm_err.ollama_running,
                requested_model=llm_err.requested_model,
                available_models=llm_err.available_models,
                audio_url=err_audio_url,
            )

        # 4. Post-LLM Floor Guard
        current_stage = "auth"
        if command_data.floor not in auth_user.allowed_floors:
            detail_msg = format_floor_forbidden_detail({command_data.floor}, auth_user.allowed_floors)
            err_audio_url, _ = await _prepare_user_audio_response(
                COMMON_ERROR_TTS_PHRASES["floor_forbidden"], auth_user.username
            )
            return build_error_response(
                status_code=403,
                detail=detail_msg,
                stage="auth",
                error_code="FLOOR_FORBIDDEN",
                audio_url=err_audio_url,
                headers=NO_STORE_HEADERS,
            )

        # 5. Real Home Assistant REST API Dispatch (Honest Error Reporting - No Fake Simulation)
        current_stage = "ha"
        try:
            ha_result = await asyncio.to_thread(
                execute_home_assistant_service, command_data, set(auth_user.allowed_floors)
            )
        except PermissionError:
            return build_error_response(
                status_code=403,
                detail=format_floor_forbidden_detail({command_data.floor}, auth_user.allowed_floors),
                stage="auth",
                error_code="FLOOR_FORBIDDEN",
                headers=NO_STORE_HEADERS,
            )
        except HAServiceError as ha_err:
            err_audio_url, _ = await _prepare_user_audio_response(ha_err.detail, auth_user.username)
            return build_error_response(
                status_code=ha_err.status_code,
                detail=ha_err.detail,
                stage="ha",
                error_code=ha_err.error_code,
                audio_url=err_audio_url,
                headers=NO_STORE_HEADERS,
            )

        # 6. Hybrid TTS (Edge-TTS primary + Local Piper fallback)
        current_stage = "tts"
        audio_url, tts_status = await _prepare_user_audio_response(
            command_data.success_message, auth_user.username
        )

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "transcript": transcript,
                "parsed_command": command_data.model_dump(),
                "llm_model": getattr(command_data, "_model_used", None) or OLLAMA_MODEL,
                "ha_execution": ha_result,
                "audio_url": audio_url,
                "tts_status": tts_status,
            },
            headers=NO_STORE_HEADERS,
        )

    except GatewayAuthError as auth_err:
        return build_error_response(
            status_code=auth_err.status_code,
            detail=auth_err.detail,
            stage=auth_err.stage,
            error_code=auth_err.error_code,
            headers=auth_err.headers or NO_STORE_HEADERS,
        )
    except Exception as exc:
        logger.exception(f"Unexpected error in /process-audio at stage '{current_stage}': {exc}")
        return build_error_response(
            status_code=500,
            detail=f"خطای غیرمنتظره در پردازش فرمان صوتی (مرحله {current_stage}).",
            stage=current_stage,
            error_code="PROCESS_AUDIO_INTERNAL_ERROR",
        )
    finally:
        if audio_obj is not None and _is_upload_file_obj(audio_obj):
            close_audio = getattr(audio_obj, "close", None)
            if callable(close_audio):
                try:
                    await close_audio()
                except Exception:
                    pass
        if form_data is not None:
            close_form = getattr(form_data, "close", None)
            if callable(close_form):
                try:
                    await close_form()
                except Exception:
                    pass
        if temp_input_path is not None and temp_input_path.exists():
            try:
                temp_input_path.unlink(missing_ok=True)
            except OSError:
                pass


@app.get("/play-response")
async def play_response(
    id: Optional[str] = Query(default=None),
    auth_user: AuthUser = Depends(require_authenticated_user),
):
    """Streams owner-isolated MP3 (Edge-TTS) or WAV (Piper) response back to client."""
    if id is not None:
        if not re.fullmatch(r"[a-fA-F0-9]{1,32}", id):
            return build_error_response(
                status_code=400,
                detail="شناسه فایل صوتی نامعتبر است.",
                stage="tts",
                error_code="INVALID_AUDIO_ID",
                headers=NO_STORE_HEADERS,
            )
        id_norm = id.lower()
        base_dir = TEMP_AUDIO_DIR.resolve()

        candidate: Optional[Path] = None
        for ext in (".mp3", ".wav"):
            probe = (base_dir / f"response_{id_norm}{ext}").resolve()
            if probe.parent == base_dir and probe.exists() and probe.stat().st_size > 0:
                candidate = probe
                break

        owner = _get_audio_owner(id_norm)
        if owner is not None and auth_user.role != "admin" and owner != auth_user.username:
            return build_error_response(
                status_code=403,
                detail="شما مجاز به دسترسی به این فایل صوتی نیستید.",
                stage="auth",
                error_code="AUDIO_FORBIDDEN",
                headers=NO_STORE_HEADERS,
            )
        if owner is None and candidate is not None and auth_user.role != "admin":
            return build_error_response(
                status_code=403,
                detail="شما مجاز به دسترسی به این فایل صوتی نیستید.",
                stage="auth",
                error_code="AUDIO_FORBIDDEN",
                headers=NO_STORE_HEADERS,
            )

        if candidate is not None:
            is_wav = candidate.suffix.lower() == ".wav"
            return FileResponse(
                candidate,
                media_type="audio/wav" if is_wav else "audio/mpeg",
                filename="response.wav" if is_wav else "response.mp3",
                headers=NO_STORE_HEADERS,
            )
        return build_error_response(
            status_code=404,
            detail="فایل صوتی درخواست‌شده یافت نشد.",
            stage="tts",
            error_code="AUDIO_NOT_FOUND",
            headers=NO_STORE_HEADERS,
        )

    for latest_path in _get_user_latest_audio_paths(auth_user.username):
        if latest_path.exists() and latest_path.stat().st_size > 0:
            is_wav = latest_path.suffix.lower() == ".wav"
            return FileResponse(
                latest_path,
                media_type="audio/wav" if is_wav else "audio/mpeg",
                filename="response.wav" if is_wav else "response.mp3",
                headers=NO_STORE_HEADERS,
            )

    return build_error_response(
        status_code=404,
        detail="فایل صوتی پاسخ یافت نشد.",
        stage="tts",
        error_code="AUDIO_NOT_FOUND",
        headers=NO_STORE_HEADERS,
    )


def _resolve_listen_port(host: str, requested_port: int) -> int:
    import socket

    def _try_bind(candidate: int) -> Optional[int]:
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, candidate))
                return int(sock.getsockname()[1])
            except OSError:
                return None

    bound_port = _try_bind(requested_port)
    if bound_port is not None:
        return bound_port
    for fallback_port in (p for p in (8800, 8888, 9000, 0) if p != requested_port):
        bound_port = _try_bind(fallback_port)
        if bound_port is not None:
            return bound_port
    raise RuntimeError(f"No bindable TCP port available on host '{host}'.")


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    requested_port = int(os.getenv("PORT", "8000"))
    chosen_port = _resolve_listen_port(host, requested_port)
    display_host = "localhost" if host in ("0.0.0.0", "::", "") else host
    logger.info(f"Starting Smart Building Voice Gateway at http://{display_host}:{chosen_port}")
    uvicorn.run("server:app", host=host, port=chosen_port, reload=False)
