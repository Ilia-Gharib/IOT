import os
import re
import hmac
import json
import math
import uuid
import shutil
import asyncio
import inspect
import logging
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any, List, Set, Literal

import requests
import edge_tts
import ollama
from fastapi import FastAPI, Request, Response, UploadFile, File, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel, Field, PrivateAttr, ValidationError, field_validator, model_validator
from faster_whisper import WhisperModel

# ---------------------------------------------------------------------------
# Logging & Configuration
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)
logger = logging.getLogger("SmartBuildingVoiceGateway")

# تنظیمات مدل‌ها (قابل تغییر از طریق متغیرهای محیطی)
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "base")  # "base" یا "small"
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")        # مثلاً qwen2.5:7b یا qwen3
_INITIAL_OLLAMA_FALLBACK_ENABLED = (
    os.getenv("OLLAMA_FALLBACK_ENABLED", "true").strip().lower() == "true"
)
OLLAMA_FALLBACK_ENABLED = _INITIAL_OLLAMA_FALLBACK_ENABLED
OLLAMA_LIST_TIMEOUT = float(os.getenv("OLLAMA_LIST_TIMEOUT", "5.0"))
OLLAMA_CHAT_TIMEOUT = float(os.getenv("OLLAMA_CHAT_TIMEOUT", "60.0"))
TTS_VOICE = os.getenv("TTS_VOICE", "fa-IR-DilaraNeural")      # یا "fa-IR-FaridNeural"
TTS_TIMEOUT_SECONDS = float(os.getenv("TTS_TIMEOUT_SECONDS", "15.0"))

TTS_FALLBACK_MESSAGE = (
    "پاسخ متنی آماده است؛ تولید صدای پاسخ با سرویس Edge-TTS به دلیل خطای شبکه یا سرویس در دسترس نبود."
)

# محدودیت‌های امنیتی آپلود، کش و دسترسی
MAX_AUDIO_UPLOAD_BYTES = int(os.getenv("MAX_AUDIO_UPLOAD_BYTES", str(10 * 1024 * 1024)))
ALLOWED_AUDIO_SUFFIXES = {".webm", ".wav", ".ogg", ".mp3", ".m4a", ".flac", ".opus"}
MAX_CACHED_TTS_FILES = int(os.getenv("MAX_CACHED_TTS_FILES", "20"))

CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",")
    if origin.strip()
]
CORS_ALLOW_CREDENTIALS = (
    os.getenv("CORS_ALLOW_CREDENTIALS", "false").strip().lower() == "true"
    and "*" not in CORS_ORIGINS
)

_INITIAL_VOICE_API_TOKEN = os.getenv("VOICE_API_TOKEN", "").strip()
VOICE_API_TOKEN = _INITIAL_VOICE_API_TOKEN

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

def _parse_allowed_entities(raw_value: str) -> Set[str]:
    return {item.strip().lower() for item in raw_value.split(",") if item.strip()}


_INITIAL_ALLOWED_ENTITIES = _parse_allowed_entities(os.getenv("ALLOWED_ENTITIES", ""))
ALLOWED_ENTITIES: Set[str] = set(_INITIAL_ALLOWED_ENTITIES)

# تنظیمات کانتینرهای Home Assistant به تفکیک طبقه
HA_INSTANCES = {
    1: {
        "url": os.getenv("HA_FLOOR1_URL", "http://localhost:8501"),
        "token": os.getenv("HA_FLOOR1_TOKEN", "YOUR_FLOOR_1_LONG_LIVED_ACCESS_TOKEN"),
    },
    2: {
        "url": os.getenv("HA_FLOOR2_URL", "http://localhost:8502"),
        "token": os.getenv("HA_FLOOR2_TOKEN", "YOUR_FLOOR_2_LONG_LIVED_ACCESS_TOKEN"),
    },
}

TEMP_AUDIO_DIR = Path(tempfile.gettempdir()) / "smart_building_audio"


def _ensure_temp_audio_dir(target_dir: Optional[Path] = None) -> Path:
    """Safely creates TEMP_AUDIO_DIR (rejects symlinks; restricts mode to 0700 on POSIX)."""
    resolved_target = TEMP_AUDIO_DIR if target_dir is None else target_dir
    if resolved_target.is_symlink():
        raise RuntimeError("Temporary audio directory must not be a symbolic link.")
    resolved_target.mkdir(parents=True, exist_ok=True)
    if resolved_target.is_symlink():
        raise RuntimeError("Temporary audio directory must not be a symbolic link.")
    if os.name != "nt":
        os.chmod(resolved_target, 0o700)
    return resolved_target


_ensure_temp_audio_dir()
LATEST_AUDIO_PATH = TEMP_AUDIO_DIR / "latest_response.mp3"

whisper_model: Optional[WhisperModel] = None
whisper_lock = threading.Lock()

# ثبت رفرنس پیش‌فرض SDK برای حفظ سازگاری با تست‌ها هنگام monkeypatch
_DEFAULT_OLLAMA_LIST = ollama.list
_DEFAULT_OLLAMA_CHAT = ollama.chat


# ---------------------------------------------------------------------------
# Security & Sanitization Helpers
# ---------------------------------------------------------------------------
def _get_allowed_entities() -> Set[str]:
    if ALLOWED_ENTITIES != _INITIAL_ALLOWED_ENTITIES:
        return {str(e).strip().lower() for e in ALLOWED_ENTITIES if str(e).strip()}
    return _parse_allowed_entities(os.getenv("ALLOWED_ENTITIES", ""))


def _get_voice_api_token() -> str:
    if VOICE_API_TOKEN != _INITIAL_VOICE_API_TOKEN:
        return (VOICE_API_TOKEN or "").strip()
    return os.getenv("VOICE_API_TOKEN", "").strip()


def _is_ollama_fallback_enabled() -> bool:
    if OLLAMA_FALLBACK_ENABLED != _INITIAL_OLLAMA_FALLBACK_ENABLED:
        return bool(OLLAMA_FALLBACK_ENABLED)
    return os.getenv("OLLAMA_FALLBACK_ENABLED", "true").strip().lower() == "true"


def _sanitize_for_log(text: Any, max_len: int = 500) -> str:
    """Strips CR/LF and control characters from untrusted values before logging."""
    cleaned = re.sub(r"[\r\n\x00-\x1f\x7f]+", " ", str(text or "")).strip()
    if len(cleaned) > max_len:
        return f"{cleaned[:max_len]}..."
    return cleaned


def _apply_security_headers(response: Response) -> Response:
    """Attaches standard defensive HTTP headers (and CSP on HTML responses)."""
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
            "connect-src 'self';"
        )
    return response


# ---------------------------------------------------------------------------
# Domain Exceptions & Standardized Error Payloads
# ---------------------------------------------------------------------------
class STTServiceError(Exception):
    """Raised when Faster-Whisper transcription fails."""

    def __init__(
        self,
        detail: str,
        status_code: int = 500,
        error_code: str = "STT_ERROR",
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.error_code = error_code


class OllamaServiceError(Exception):
    """Raised when Ollama is unreachable, model is missing, or LLM output is invalid."""

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


def build_error_payload(
    detail: str,
    stage: str = "internal",
    error_code: str = "INTERNAL_ERROR",
    ollama_running: Optional[bool] = None,
    requested_model: Optional[str] = None,
    available_models: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Builds a consistent JSON error envelope for all endpoints and exception handlers."""
    return {
        "status": "error",
        "detail": detail,
        "stage": stage,
        "error_code": error_code,
        "ollama_running": ollama_running,
        "requested_model": requested_model,
        "available_models": list(available_models) if available_models is not None else [],
    }


def build_error_response(
    status_code: int,
    detail: str,
    stage: str = "internal",
    error_code: str = "INTERNAL_ERROR",
    ollama_running: Optional[bool] = None,
    requested_model: Optional[str] = None,
    available_models: Optional[List[str]] = None,
) -> JSONResponse:
    """Returns a FastAPI JSONResponse using the standardized error payload."""
    return JSONResponse(
        status_code=status_code,
        content=build_error_payload(
            detail=detail,
            stage=stage,
            error_code=error_code,
            ollama_running=ollama_running,
            requested_model=requested_model,
            available_models=available_models,
        ),
    )


def _check_bearer_auth(request: Request) -> Optional[JSONResponse]:
    """Enforces Bearer token auth when VOICE_API_TOKEN is configured."""
    expected_token = _get_voice_api_token()
    if not expected_token:
        return None

    auth_header = request.headers.get("Authorization", "")
    scheme, _, token = auth_header.partition(" ")
    if (
        scheme.lower() != "bearer"
        or not token.strip()
        or not hmac.compare_digest(token.strip(), expected_token)
    ):
        logger.warning(f"Unauthorized request on {request.method} {request.url.path}")
        return build_error_response(
            status_code=401,
            detail="احراز هویت نامعتبر است یا توکن دسترسی ارسال نشده است.",
            stage="auth",
            error_code="UNAUTHORIZED",
        )
    return None


# ---------------------------------------------------------------------------
# Data Models
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

        allowed_entities = _get_allowed_entities()
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
# System Prompt for Qwen
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are an AI IoT Orchestrator for a two-floor smart building.
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
{
  "floor": 1 or 2 (int, default to 1 if not mentioned),
  "domain": "light" | "switch" | "climate" | "cover",
  "service": "turn_on" | "turn_off" | "set_temperature" | "toggle",
  "entity_id": "<domain>.<object_id>",
  "service_data": {} (optional dict, e.g. {"temperature": 24} if service is "set_temperature"),
  "ack_message": "Natural Persian immediate acknowledgment (e.g., در حال روشن کردن چراغ پذیرایی طبقه اول...)",
  "success_message": "Natural, polite Persian confirmation to be read aloud after execution (e.g., چراغ پذیرایی در طبقه اول با موفقیت روشن شد.)"
}
2. Do not wrap the JSON in markdown blocks or add any extra commentary.
"""


# ---------------------------------------------------------------------------
# Lifespan: Warm-up Faster-Whisper
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    global whisper_model
    logger.info(f"Loading Faster-Whisper model ('{WHISPER_MODEL_SIZE}')...")
    try:
        # تلاش برای بارگذاری روی GPU (CUDA) و سوییچ خودکار به CPU در صورت عدم وجود
        whisper_model = await asyncio.to_thread(
            lambda: WhisperModel(WHISPER_MODEL_SIZE, device="auto", compute_type="auto")
        )
        logger.info("Faster-Whisper loaded successfully.")
    except Exception as e:
        logger.warning(f"Auto device initialization failed ({e}). Falling back to CPU (int8)...")
        try:
            whisper_model = await asyncio.to_thread(
                lambda: WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
            )
            logger.info("Faster-Whisper loaded on CPU.")
        except Exception as cpu_err:
            logger.exception(f"Faster-Whisper CPU fallback initialization failed: {cpu_err}")
            whisper_model = None

    yield

    # پاکسازی فایل‌های صوتی موقت هنگام خاموش شدن سرور
    shutil.rmtree(TEMP_AUDIO_DIR, ignore_errors=True)
    logger.info("Cleaned up temporary audio directory.")


app = FastAPI(
    title="Smart Building Local Voice Control Gateway",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Middleware (Registered BEFORE CORSMiddleware so CORS wraps outside it)
# ---------------------------------------------------------------------------
@app.middleware("http")
async def security_and_error_middleware(request: Request, call_next):
    """Enforces optional Bearer auth, keeps unhandled exceptions inside CORS, and applies security headers."""
    try:
        if request.method != "OPTIONS" and request.url.path in ("/process-audio", "/play-response"):
            auth_error = _check_bearer_auth(request)
            if auth_error is not None:
                return _apply_security_headers(auth_error)

        response = await call_next(request)
    except Exception as exc:
        logger.exception(
            f"Unhandled exception inside middleware on {request.method} {request.url.path}: {exc}"
        )
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


# ---------------------------------------------------------------------------
# Global JSON Exception Handlers
# ---------------------------------------------------------------------------
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Ensures all HTTP errors return a structured JSON response instead of plain text or HTML."""
    detail = exc.detail
    if isinstance(detail, dict):
        detail_msg = str(detail.get("detail") or detail)
    else:
        detail_msg = str(detail)

    logger.warning(f"HTTP {exc.status_code} on {request.method} {request.url.path}: {_sanitize_for_log(detail_msg)}")
    return build_error_response(
        status_code=exc.status_code,
        detail=detail_msg,
        stage="internal",
        error_code=f"HTTP_{exc.status_code}",
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Ensures FastAPI request validation errors return structured JSON with a readable string detail."""
    err_fields: List[str] = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ()))
        msg = str(err.get("msg", "Invalid input"))
        err_fields.append(f"{loc}: {msg}" if loc else msg)
    summary = "; ".join(err_fields) if err_fields else "Invalid request parameters."
    stage = "upload" if request.url.path == "/process-audio" else "internal"

    logger.warning(f"Request validation error on {request.method} {request.url.path}: {_sanitize_for_log(summary)}")
    return build_error_response(
        status_code=422,
        detail="درخواست ارسالی معتبر نیست. لطفاً ورودی یا فایل صوتی ارسال‌شده را بررسی کنید.",
        stage=stage,
        error_code="VALIDATION_ERROR",
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catches any unhandled exception globally so FastAPI never emits plain-text 500 errors."""
    logger.exception(f"Unhandled exception on {request.method} {request.url.path}: {exc}")
    return build_error_response(
        status_code=500,
        detail="خطای غیرمنتظره در سرور رخ داد. لطفاً لاگ‌های سرور را بررسی کنید.",
        stage="internal",
        error_code="INTERNAL_SERVER_ERROR",
    )


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------
def _is_cuda_runtime_error(exc: Exception) -> bool:
    """Detects CUDA/cuBLAS/cuDNN runtime errors during Whisper inference."""
    msg = str(exc).lower()
    cuda_markers = (
        "cublas",
        "cudnn",
        "cuda",
        "nvcuda",
        "cudart",
        "libcublas",
        "libcudnn",
        "cublas64",
        "cudnn64",
    )
    return any(marker in msg for marker in cuda_markers)


def _run_whisper_transcribe(model: WhisperModel, audio_path: Path) -> str:
    """Executes Faster-Whisper transcription and eagerly consumes the segment generator."""
    segments, info = model.transcribe(
        str(audio_path),
        language="fa",
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=400),
    )
    transcript = " ".join(segment.text.strip() for segment in segments).strip()
    lang_prob = getattr(info, "language_probability", 0.0)
    logger.info(f"Transcribed Speech ({lang_prob:.2f}): {_sanitize_for_log(transcript)}")
    return transcript


def transcribe_persian_audio(audio_path: Path) -> str:
    """Transcribes audio file to Persian text using Faster-Whisper with automatic CUDA->CPU fallback."""
    global whisper_model

    with whisper_lock:
        current_model = whisper_model

    if current_model is None:
        logger.error("Whisper model is not initialized.")
        raise STTServiceError(
            detail="مدل تبدیل گفتار به متن (Faster-Whisper) بارگذاری نشده است.",
            status_code=503,
            error_code="STT_MODEL_NOT_INITIALIZED",
        )

    try:
        return _run_whisper_transcribe(current_model, audio_path)
    except Exception as exc:
        if _is_cuda_runtime_error(exc):
            logger.warning(
                f"Faster-Whisper encountered CUDA runtime error ({exc}). "
                "Re-initializing model on CPU (int8) and retrying..."
            )
            try:
                with whisper_lock:
                    if whisper_model is current_model or whisper_model is None:
                        whisper_model = WhisperModel(
                            WHISPER_MODEL_SIZE, device="cpu", compute_type="int8"
                        )
                        logger.info("Faster-Whisper re-initialized on CPU (int8).")
                    retry_model = whisper_model
                return _run_whisper_transcribe(retry_model, audio_path)
            except Exception as retry_exc:
                logger.exception(f"Whisper CPU fallback transcription failed: {retry_exc}")
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


def _close_ollama_client(client: Any) -> None:
    """Cleanly closes a temporary ollama.Client's underlying HTTP client."""
    close_fn = getattr(client, "close", None)
    if not callable(close_fn):
        close_fn = getattr(getattr(client, "_client", None), "close", None)
    if callable(close_fn):
        try:
            close_fn()
        except Exception:
            pass


def _call_ollama_list() -> Any:
    """Calls Ollama list endpoint with bounded timeout, preserving monkeypatched ollama.list in tests."""
    if ollama.list is not _DEFAULT_OLLAMA_LIST:
        return ollama.list()
    client = ollama.Client(timeout=OLLAMA_LIST_TIMEOUT)
    try:
        return client.list()
    finally:
        _close_ollama_client(client)


def _supports_think_kwarg(fn: Any) -> bool:
    """Returns True if callable accepts a 'think' keyword argument or **kwargs."""
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
    """Calls Ollama chat endpoint with bounded timeout and safe think=False handling."""
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
                logger.debug(
                    f"Ollama chat rejected 'think=False' ({exc}); retrying without 'think'."
                )
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
    """Detects network/timeout failures across stdlib, ollama, and httpx exceptions."""
    if isinstance(exc, (ConnectionError, TimeoutError, ollama.RequestError, OSError)):
        return True
    mod = getattr(type(exc), "__module__", "") or ""
    return mod.startswith("httpx")


def _extract_model_names(list_response: Any) -> List[str]:
    """Extracts installed model names from both ollama==0.6.x ListResponse and 0.3.x dict shapes."""
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


def _is_model_available(requested_model: str, available_models: List[str]) -> bool:
    """Checks exact match or '<model>:latest' when no tag was specified."""
    if requested_model in available_models:
        return True
    if ":" not in requested_model and f"{requested_model}:latest" in available_models:
        return True
    return False


def _select_ollama_model(
    requested_model: str,
    available_models: List[str],
    fallback_enabled: Optional[bool] = None,
) -> Optional[str]:
    """Selects exact/:latest match, or best installed fallback when OLLAMA_FALLBACK_ENABLED is True."""
    if not available_models:
        return None

    if requested_model in available_models:
        return requested_model
    if ":" not in requested_model and f"{requested_model}:latest" in available_models:
        return f"{requested_model}:latest"

    use_fallback = _is_ollama_fallback_enabled() if fallback_enabled is None else bool(fallback_enabled)
    if not use_fallback:
        return None

    requested_base = requested_model.split(":", 1)[0].strip().lower()
    if requested_base:
        for candidate in available_models:
            if candidate.split(":", 1)[0].strip().lower() == requested_base:
                return candidate
        for candidate in available_models:
            if candidate.split(":", 1)[0].strip().lower().startswith(requested_base):
                return candidate

    for candidate in available_models:
        candidate_base = candidate.split(":", 1)[0].strip().lower()
        if candidate_base.startswith("qwen") or candidate.strip().lower().startswith("qwen"):
            return candidate

    return available_models[0]


def _extract_chat_content(response: Any) -> str:
    """Extracts text content from both ollama==0.6.x ChatResponse objects and dicts."""
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

    try:
        return str(response["message"]["content"] or "")
    except (KeyError, TypeError, IndexError):
        return ""


def _normalize_floor(raw_floor: Any) -> int:
    """Normalizes floor value (1, 2, 1.0, 2.0, '1', '2', Persian/Arabic digits) to 1 or 2, defaulting to 1."""
    if isinstance(raw_floor, bool):
        return 1
    if isinstance(raw_floor, int) and raw_floor in (1, 2):
        return raw_floor
    if isinstance(raw_floor, float) and raw_floor in (1.0, 2.0):
        return int(raw_floor)
    if isinstance(raw_floor, str):
        digit_map = str.maketrans("۱۲١٢", "1212")
        stripped = raw_floor.strip().translate(digit_map)
        if stripped in ("1", "2"):
            return int(stripped)
    return 1


def _select_preferred_command_dict(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Prefers valid LLMCommandSchema dicts, then dicts with command keys, else the first dict."""
    for candidate in candidates:
        try:
            probe = dict(candidate)
            probe["floor"] = _normalize_floor(probe.get("floor"))
            LLMCommandSchema(**probe)
            return candidate
        except (ValidationError, TypeError, ValueError):
            continue

    command_keys = {"domain", "service", "entity_id"}
    for candidate in candidates:
        if command_keys.issubset(candidate.keys()):
            return candidate

    return candidates[0]


def extract_json_from_llm(raw_content: str) -> Dict[str, Any]:
    """Parses a JSON object from raw LLM output, stripping <think> tags and Markdown fences."""
    if not isinstance(raw_content, str) or not raw_content.strip():
        raise ValueError("Empty LLM response content.")

    cleaned = re.sub(
        r"<think>.*?</think>", "", raw_content, flags=re.DOTALL | re.IGNORECASE
    ).strip()
    if "</think>" in cleaned.lower():
        cleaned = re.split(r"</think>", cleaned, flags=re.IGNORECASE)[-1].strip()

    # ۱. تلاش مستقیم برای پارس JSON خالص
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # ۲. استخراج از بلاک‌های Markdown (```json ... ``` یا ``` ... ```)
    fence_pattern = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)
    fence_candidates: List[Dict[str, Any]] = []
    for match in fence_pattern.finditer(cleaned):
        candidate = match.group(1).strip()
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                fence_candidates.append(parsed)
        except json.JSONDecodeError:
            pass
    if fence_candidates:
        return _select_preferred_command_dict(fence_candidates)

    # ۳. اسکن برای آبجکت‌های JSON در متن و اولویت‌بندی ساختار فرمان معتبر
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
        return _select_preferred_command_dict(scan_candidates)

    raise ValueError("No valid JSON object found in LLM output.")


def reason_with_qwen(transcript: str) -> LLMCommandSchema:
    """Sends transcribed Persian text to local Ollama (Qwen) and parses strict JSON."""
    # ۱. بررسی دسترس‌پذیری سرویس Ollama و دریافت لیست مدل‌ها
    try:
        list_response = _call_ollama_list()
    except Exception as exc:
        logger.exception(f"Ollama reachability check failed: {exc}")
        raise OllamaServiceError(
            detail=(
                "سرویس Ollama در دسترس نیست یا در حال اجرا نمی‌باشد. لطفاً اجرای سرویس Ollama را بررسی کنید. "
                "(Ollama service is unreachable.)"
            ),
            status_code=503,
            error_code="OLLAMA_UNREACHABLE",
            ollama_running=False,
            requested_model=OLLAMA_MODEL,
            available_models=[],
        ) from exc

    available_models = _extract_model_names(list_response)

    # ۲. انتخاب مدل فعال (تطبیق دقیق، :latest یا fallback به مدل نصب‌شده در صورت فعال‌بودن)
    active_model = _select_ollama_model(OLLAMA_MODEL, available_models)
    if active_model is None:
        if available_models:
            models_list_text = "، ".join(available_models)
            detail_msg = (
                f"سرویس Ollama در حال اجرا است اما مدل «{OLLAMA_MODEL}» یافت نشد. "
                f"مدل‌های موجود: {models_list_text}."
            )
        else:
            detail_msg = (
                f"سرویس Ollama در حال اجرا است اما هیچ مدلی نصب نشده و مدل «{OLLAMA_MODEL}» یافت نشد."
            )
        logger.warning(
            f"Ollama running, requested model '{OLLAMA_MODEL}' not found. Available: {available_models}"
        )
        raise OllamaServiceError(
            detail=detail_msg,
            status_code=404,
            error_code="MODEL_NOT_FOUND",
            ollama_running=True,
            requested_model=OLLAMA_MODEL,
            available_models=available_models,
        )

    is_latest_alias = ":" not in OLLAMA_MODEL and active_model == f"{OLLAMA_MODEL}:latest"
    if active_model != OLLAMA_MODEL and not is_latest_alias:
        logger.warning(
            f"Requested Ollama model '{OLLAMA_MODEL}' not found in {available_models}; "
            f"falling back to '{active_model}'."
        )

    # ۳. فراخوانی چت با مدل محلی Ollama
    logger.info(f"Querying Ollama model '{active_model}'...")
    try:
        response = _call_ollama_chat(
            model=active_model,
            format="json",
            options={"temperature": 0.1},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": transcript},
            ],
        )
    except ollama.ResponseError as exc:
        logger.exception(f"Ollama chat ResponseError for model '{active_model}': {exc}")
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
            logger.exception(f"Ollama connection failure during chat with '{active_model}': {exc}")
            raise OllamaServiceError(
                detail="ارتباط با سرویس Ollama حین پردازش فرمان قطع شد یا با وقفه زمانی مواجه گردید.",
                status_code=503,
                error_code="OLLAMA_UNREACHABLE",
                ollama_running=False,
                requested_model=active_model,
                available_models=available_models,
            ) from exc
        logger.exception(f"Unexpected error calling Ollama chat for model '{active_model}': {exc}")
        raise OllamaServiceError(
            detail=f"خطای غیرمنتظره در فراخوانی مدل Ollama ({active_model}).",
            status_code=502,
            error_code="OLLAMA_CHAT_ERROR",
            ollama_running=True,
            requested_model=active_model,
            available_models=available_models,
        ) from exc

    raw_content = _extract_chat_content(response)
    logger.info(f"Ollama JSON Output ({active_model}): {_sanitize_for_log(raw_content)}")

    # ۴. استخراج JSON و اعتبارسنجی سخت‌گیرانه اسکیما
    try:
        parsed_dict = extract_json_from_llm(raw_content)
        parsed_dict["floor"] = _normalize_floor(parsed_dict.get("floor"))
        command = LLMCommandSchema(**parsed_dict)
        command._model_used = active_model
        return command
    except (ValueError, json.JSONDecodeError, ValidationError, TypeError) as parse_exc:
        logger.exception(
            f"Failed to parse or validate LLM JSON output for model '{active_model}'. "
            f"Raw={_sanitize_for_log(raw_content)!r} | Error={parse_exc}"
        )
        raise OllamaServiceError(
            detail=f"خروجی مدل زبانی ({active_model}) ساختار معتبر JSON فرمان خانه هوشمند نداشت.",
            status_code=502,
            error_code="LLM_INVALID_JSON",
            ollama_running=True,
            requested_model=active_model,
            available_models=available_models,
        ) from parse_exc


def execute_home_assistant_service(cmd: LLMCommandSchema) -> Dict[str, Any]:
    """Dispatches REST API call to the target floor's Home Assistant Docker container."""
    ha_target = HA_INSTANCES.get(cmd.floor, HA_INSTANCES[1])
    base_url = ha_target["url"].rstrip("/")
    token = ha_target["token"]
    parsed_url = urlparse(base_url)
    floor_port = parsed_url.port or (8502 if cmd.floor == 2 else 8501)

    if cmd.domain not in ALLOWED_DOMAINS:
        raise ValueError(f"Disallowed Home Assistant domain: {cmd.domain}")
    if cmd.service not in ALLOWED_SERVICES_BY_DOMAIN.get(cmd.domain, set()):
        raise ValueError(f"Disallowed Home Assistant service '{cmd.service}' for domain '{cmd.domain}'")
    if not re.fullmatch(rf"^{re.escape(cmd.domain)}\.[a-z0-9_]+$", cmd.entity_id):
        raise ValueError(f"Invalid Home Assistant entity_id: {cmd.entity_id}")

    allowed_entities = _get_allowed_entities()
    if allowed_entities and cmd.entity_id not in allowed_entities:
        raise ValueError(f"Entity '{cmd.entity_id}' is not in ALLOWED_ENTITIES")

    endpoint = f"{base_url}/api/services/{cmd.domain}/{cmd.service}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
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
        if resp.status_code >= 300:
            raise requests.exceptions.HTTPError(
                f"Unexpected HTTP {resp.status_code} from Home Assistant",
                response=resp,
            )
        return {
            "ok": True,
            "status_code": resp.status_code,
            "simulated": False,
            "floor_port": floor_port,
        }
    except requests.exceptions.HTTPError as http_err:
        status_code = http_err.response.status_code if http_err.response is not None else None
        logger.warning(f"Home Assistant HTTP error on Floor {cmd.floor}: status={status_code}")
        return {
            "ok": False,
            "simulated": False,
            "status_code": status_code,
            "floor_port": floor_port,
            "error": f"HTTP {status_code}" if status_code is not None else "HTTP error",
        }
    except requests.exceptions.RequestException as exc:
        logger.warning(f"Home Assistant connection warning on Floor {cmd.floor}: {exc}")
        # در صورتی که کانتینر HA در محیط تست خاموش باشد، خطا ثبت شده اما چرخه صوتی متوقف نمی‌شود
        return {
            "ok": False,
            "simulated": True,
            "status_code": None,
            "floor_port": floor_port,
            "error": "Home Assistant unreachable",
        }


def _format_tts_error_label(exc: Exception) -> str:
    """Produces a clean client-safe TTS error label without socket or path details."""
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "Edge-TTS request timed out"
    exc_name = type(exc).__name__
    if exc_name == "NoAudioReceived":
        return "Edge-TTS NoAudioReceived"
    if isinstance(exc, ValueError):
        return "Empty TTS input text"
    if "empty" in str(exc).lower():
        return "Empty TTS output audio"
    return f"Edge-TTS error ({exc_name})"


def _update_latest_audio_atomically(source_path: Path) -> None:
    """Copies MP3 to LATEST_AUDIO_PATH atomically using a temporary file and os.replace."""
    target_dir = LATEST_AUDIO_PATH.parent
    _ensure_temp_audio_dir(target_dir)
    fd, tmp_name = tempfile.mkstemp(prefix=".latest_", suffix=".mp3.tmp", dir=target_dir)
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        shutil.copyfile(source_path, tmp_path)
        os.replace(tmp_path, LATEST_AUDIO_PATH)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _prune_old_tts_files(keep_count: int = MAX_CACHED_TTS_FILES) -> None:
    """Keeps only the newest keep_count cached response_*.mp3 files."""
    if not TEMP_AUDIO_DIR.exists():
        return
    limit = max(0, keep_count)
    files_with_mtime: List[tuple[int, Path]] = []
    for path in TEMP_AUDIO_DIR.glob("response_*.mp3"):
        try:
            files_with_mtime.append((path.stat().st_mtime_ns, path))
        except OSError:
            continue
    files_with_mtime.sort(key=lambda item: item[0], reverse=True)
    for _, stale_file in files_with_mtime[limit:]:
        try:
            stale_file.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(f"Failed to prune cached TTS file '{stale_file.name}': {exc}")


async def synthesize_persian_tts(text: str, output_path: Path) -> Dict[str, Any]:
    """Synthesizes Persian text into an MP3 file using Edge-TTS with timeout and resilient fallback."""
    logger.info(f"Synthesizing TTS with voice '{TTS_VOICE}': {_sanitize_for_log(text)}")
    try:
        if not text or not text.strip():
            raise ValueError("TTS input text is empty.")

        communicate = edge_tts.Communicate(text=text, voice=TTS_VOICE)
        await asyncio.wait_for(
            communicate.save(str(output_path)),
            timeout=TTS_TIMEOUT_SECONDS,
        )

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError("Edge-TTS produced an empty or missing audio file.")

        return {
            "ok": True,
            "voice": TTS_VOICE,
            "fallback_message": None,
            "error": None,
        }
    except Exception as exc:
        logger.exception(f"Edge-TTS synthesis failed for voice '{TTS_VOICE}': {exc}")
        if output_path.exists():
            try:
                output_path.unlink(missing_ok=True)
            except OSError as unlink_err:
                logger.warning(f"Could not clean up failed TTS output '{output_path.name}': {unlink_err}")

        return {
            "ok": False,
            "voice": TTS_VOICE,
            "fallback_message": TTS_FALLBACK_MESSAGE,
            "error": _format_tts_error_label(exc),
        }


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def serve_pwa_client():
    """Serves the mobile web interface directly."""
    index_file = Path(__file__).parent / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h3>index.html not found next to server.py</h3>", status_code=404)
    html_content = await asyncio.to_thread(index_file.read_text, encoding="utf-8")
    return HTMLResponse(content=html_content)


@app.post("/process-audio")
async def process_audio(audio: UploadFile = File(...)):
    """
    Main pipeline:
    1. Save uploaded WebM/WAV audio to a temporary file
    2. STT via Faster-Whisper (language='fa')
    3. Intent parsing via local Ollama (Qwen)
    4. REST API dispatch to Floor 1 or Floor 2 Home Assistant instance
    5. TTS generation via Edge-TTS
    """
    temp_input_path: Optional[Path] = None
    current_stage = "upload"

    try:
        # ۱. دریافت و ذخیره جریانی فایل صوتی با محدودیت حجم و پسوند مجاز
        try:
            first_chunk = await audio.read(65536)
        except Exception as read_exc:
            logger.exception(f"Failed to read uploaded audio stream: {read_exc}")
            return build_error_response(
                status_code=400,
                detail="دریافت یا خواندن فایل صوتی ارسالی ناموفق بود.",
                stage="upload",
                error_code="AUDIO_UPLOAD_ERROR",
            )

        if not first_chunk:
            logger.warning("Received empty audio upload on /process-audio.")
            return build_error_response(
                status_code=400,
                detail="فایل صوتی ارسالی خالی است.",
                stage="upload",
                error_code="EMPTY_AUDIO_FILE",
            )

        if len(first_chunk) > MAX_AUDIO_UPLOAD_BYTES:
            logger.warning(f"Audio upload first chunk exceeded max size ({len(first_chunk)} > {MAX_AUDIO_UPLOAD_BYTES}).")
            return build_error_response(
                status_code=413,
                detail="حجم فایل صوتی ارسالی بیشتر از حد مجاز است.",
                stage="upload",
                error_code="PAYLOAD_TOO_LARGE",
            )

        raw_suffix = Path(audio.filename or "recording.webm").suffix.lower()
        suffix = raw_suffix if raw_suffix in ALLOWED_AUDIO_SUFFIXES else ".webm"

        temp_input_fd = -1
        try:
            _ensure_temp_audio_dir()
            temp_input_fd, temp_input_path_str = tempfile.mkstemp(suffix=suffix, dir=TEMP_AUDIO_DIR)
            temp_input_path = Path(temp_input_path_str)
            total_bytes = 0

            with os.fdopen(temp_input_fd, "wb") as tmp_file:
                temp_input_fd = -1
                chunk = first_chunk
                while chunk:
                    total_bytes += len(chunk)
                    if total_bytes > MAX_AUDIO_UPLOAD_BYTES:
                        logger.warning(
                            f"Audio upload exceeded max size ({total_bytes} > {MAX_AUDIO_UPLOAD_BYTES} bytes)."
                        )
                        return build_error_response(
                            status_code=413,
                            detail="حجم فایل صوتی ارسالی بیشتر از حد مجاز است.",
                            stage="upload",
                            error_code="PAYLOAD_TOO_LARGE",
                        )
                    await asyncio.to_thread(tmp_file.write, chunk)
                    try:
                        chunk = await audio.read(65536)
                    except Exception as stream_exc:
                        logger.exception(f"Audio upload stream failed mid-transfer: {stream_exc}")
                        return build_error_response(
                            status_code=400,
                            detail="ارتباط هنگام دریافت فایل صوتی قطع شد.",
                            stage="upload",
                            error_code="AUDIO_UPLOAD_ERROR",
                        )
        except OSError as io_exc:
            logger.exception(f"Server temporary storage error during audio upload: {io_exc}")
            return build_error_response(
                status_code=500,
                detail="خطا در ذخیره‌سازی موقت فایل صوتی در سرور.",
                stage="upload",
                error_code="UPLOAD_STORAGE_ERROR",
            )
        finally:
            if temp_input_fd != -1:
                try:
                    os.close(temp_input_fd)
                except OSError:
                    pass

        # ۲. تبدیل گفتار به متن (STT)
        current_stage = "stt"
        try:
            transcript = await asyncio.to_thread(transcribe_persian_audio, temp_input_path)
        except STTServiceError as stt_err:
            return build_error_response(
                status_code=stt_err.status_code,
                detail=stt_err.detail,
                stage="stt",
                error_code=stt_err.error_code,
            )
        except Exception as stt_exc:
            logger.exception(f"Unexpected STT error: {stt_exc}")
            return build_error_response(
                status_code=500,
                detail="خطای غیرمنتظره در تبدیل گفتار به متن رخ داد.",
                stage="stt",
                error_code="STT_UNEXPECTED_ERROR",
            )

        if not transcript:
            logger.info("STT returned empty transcript (silence or unintelligible audio).")
            return build_error_response(
                status_code=422,
                detail="صدایی تشخیص داده نشد. لطفاً واضح‌تر صحبت کنید.",
                stage="stt",
                error_code="EMPTY_TRANSCRIPT",
            )

        # ۳. استنتاج مدل زبانی (Ollama Qwen)
        current_stage = "llm"
        try:
            command_data = await asyncio.to_thread(reason_with_qwen, transcript)
        except OllamaServiceError as llm_err:
            return build_error_response(
                status_code=llm_err.status_code,
                detail=llm_err.detail,
                stage="llm",
                error_code=llm_err.error_code,
                ollama_running=llm_err.ollama_running,
                requested_model=llm_err.requested_model,
                available_models=llm_err.available_models,
            )
        except Exception as llm_exc:
            logger.exception(f"Unexpected LLM reasoning error: {llm_exc}")
            return build_error_response(
                status_code=502,
                detail=f"خطا در ارتباط یا پردازش مدل Ollama ({OLLAMA_MODEL}).",
                stage="llm",
                error_code="LLM_UNEXPECTED_ERROR",
                requested_model=OLLAMA_MODEL,
            )

        # ۴. ارسال فرمان به Home Assistant طبقه مربوطه
        current_stage = "ha"
        try:
            ha_result = await asyncio.to_thread(execute_home_assistant_service, command_data)
        except Exception as ha_exc:
            logger.exception(f"Home Assistant dispatch error: {ha_exc}")
            return build_error_response(
                status_code=502,
                detail="خطا در اعتبارسنجی یا ارسال فرمان به Home Assistant.",
                stage="ha",
                error_code="HA_EXECUTION_ERROR",
            )

        # ۵. تولید فایل صوتی پاسخ (TTS) با مدیریت خطای مقاوم
        current_stage = "tts"
        response_id = uuid.uuid4().hex[:10]
        tts_output_path = TEMP_AUDIO_DIR / f"response_{response_id}.mp3"
        audio_url: Optional[str] = None
        tts_status: Dict[str, Any] = {
            "ok": False,
            "voice": TTS_VOICE,
            "fallback_message": TTS_FALLBACK_MESSAGE,
            "error": None,
        }

        try:
            _ensure_temp_audio_dir()
            tts_result = await synthesize_persian_tts(command_data.success_message, tts_output_path)

            if isinstance(tts_result, dict):
                tts_status = tts_result
            elif tts_output_path.exists() and tts_output_path.stat().st_size > 0:
                tts_status = {
                    "ok": True,
                    "voice": TTS_VOICE,
                    "fallback_message": None,
                    "error": None,
                }
            else:
                raise RuntimeError("Edge-TTS produced an empty or missing audio file.")

            if tts_status.get("ok") and tts_output_path.exists() and tts_output_path.stat().st_size > 0:
                # به‌روزرسانی اتمیک آخرین پاسخ و پاکسازی فایل‌های کش قدیمی
                await asyncio.to_thread(_update_latest_audio_atomically, tts_output_path)
                await asyncio.to_thread(_prune_old_tts_files)
                audio_url = f"/play-response?id={response_id}"
            elif tts_status.get("ok"):
                logger.warning("TTS reported ok=True, but output MP3 is missing or empty.")
                tts_status = {
                    "ok": False,
                    "voice": TTS_VOICE,
                    "fallback_message": TTS_FALLBACK_MESSAGE,
                    "error": "Empty TTS output audio",
                }
                audio_url = None
        except Exception as tts_exc:
            logger.exception(f"Edge-TTS pipeline stage failed: {tts_exc}")
            tts_status = {
                "ok": False,
                "voice": TTS_VOICE,
                "fallback_message": TTS_FALLBACK_MESSAGE,
                "error": _format_tts_error_label(tts_exc),
            }
            audio_url = None

        current_stage = "internal"
        return {
            "status": "success",
            "transcript": transcript,
            "parsed_command": command_data.model_dump(),
            "llm_model": getattr(command_data, "_model_used", None) or OLLAMA_MODEL,
            "ha_execution": ha_result,
            "audio_url": audio_url,
            "tts_status": tts_status,
        }

    except HTTPException as http_exc:
        logger.warning(f"HTTPException in /process-audio at stage '{current_stage}': {http_exc.detail}")
        return build_error_response(
            status_code=http_exc.status_code,
            detail=str(http_exc.detail),
            stage=current_stage,
            error_code=f"HTTP_{http_exc.status_code}",
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
        # حذف ایمن فایل صوتی ورودی کاربر
        if temp_input_path is not None and temp_input_path.exists():
            try:
                temp_input_path.unlink(missing_ok=True)
            except OSError as cleanup_err:
                logger.warning(f"Failed to remove temp input audio '{temp_input_path.name}': {cleanup_err}")


@app.get("/play-response")
async def play_response(id: Optional[str] = Query(default=None)):
    """Streams the synthesized MP3 response back to the mobile client."""
    if id is not None:
        if not re.fullmatch(r"[a-fA-F0-9]{1,32}", id):
            return build_error_response(
                status_code=400,
                detail="شناسه فایل صوتی نامعتبر است.",
                stage="tts",
                error_code="INVALID_AUDIO_ID",
            )
        base_dir = TEMP_AUDIO_DIR.resolve()
        candidate = (base_dir / f"response_{id}.mp3").resolve()
        if candidate.parent != base_dir:
            return build_error_response(
                status_code=400,
                detail="مسیر فایل صوتی نامعتبر است.",
                stage="tts",
                error_code="INVALID_AUDIO_PATH",
            )
        if candidate.exists() and candidate.stat().st_size > 0:
            return FileResponse(candidate, media_type="audio/mpeg", filename="response.mp3")
        return build_error_response(
            status_code=404,
            detail="فایل صوتی درخواست‌شده یافت نشد.",
            stage="tts",
            error_code="AUDIO_NOT_FOUND",
        )

    if LATEST_AUDIO_PATH.exists() and LATEST_AUDIO_PATH.stat().st_size > 0:
        return FileResponse(LATEST_AUDIO_PATH, media_type="audio/mpeg", filename="response.mp3")

    return build_error_response(
        status_code=404,
        detail="فایل صوتی پاسخ یافت نشد.",
        stage="tts",
        error_code="AUDIO_NOT_FOUND",
    )


def _resolve_listen_port(host: str, requested_port: int) -> int:
    """Tests binding (host, requested_port) and falls back to open ports on OSError."""
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

    logger.warning(
        f"Port {requested_port} on '{host}' is unavailable or restricted by OS; searching for a fallback port..."
    )
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
    logger.info(
        f"Starting Smart Building Voice Gateway at http://{display_host}:{chosen_port} (bind {host}:{chosen_port})"
    )
    uvicorn.run("server:app", host=host, port=chosen_port, reload=False)
