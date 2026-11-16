import os

# Ensure CUDA DLLs (e.g. cublas64_12.dll from Ollama or CUDA toolkit) are found by ctranslate2 / Faster-Whisper
def _ensure_cuda_dlls() -> None:
    cuda_candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\lib\ollama\cuda_v12"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\lib\ollama\cuda_v11"),
        r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.4\bin",
        r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.2\bin",
    ]
    for p in cuda_candidates:
        if os.path.isdir(p):
            try:
                os.add_dll_directory(p)
            except Exception:
                pass
            if p not in os.environ.get("PATH", ""):
                os.environ["PATH"] = p + os.pathsep + os.environ.get("PATH", "")

_ensure_cuda_dlls()

# Force offline mode for Hugging Face and Transformers if models already cached
# os.environ["HF_HUB_OFFLINE"] = "1"
# os.environ["TRANSFORMERS_OFFLINE"] = "1"

import asyncio
import hashlib
import inspect
import json
import logging
import math
import re
import secrets
import shutil
import tempfile
import threading
import time
import wave
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from dotenv import load_dotenv

# Load .env before reading module configuration
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

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
        get_user_by_email,
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
            get_user_by_email,
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
            get_user_by_email,
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

WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "large-v3-turbo").strip() or "large-v3-turbo"
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:4b").strip() or "qwen3.5:4b"
OLLAMA_FALLBACK_ENABLED = os.getenv("OLLAMA_FALLBACK_ENABLED", "false").strip().lower() == "true"
OLLAMA_LIST_TIMEOUT = float(os.getenv("OLLAMA_LIST_TIMEOUT", "5.0"))
OLLAMA_CHAT_TIMEOUT = float(os.getenv("OLLAMA_CHAT_TIMEOUT", "60.0"))

TTS_VOICE = os.getenv("TTS_VOICE", "fa_IR-amir-medium").strip() or "fa_IR-amir-medium"
TTS_TIMEOUT_SECONDS = float(os.getenv("TTS_TIMEOUT_SECONDS", "12.0"))
PIPER_MODEL_NAME = os.getenv("PIPER_MODEL_NAME", "fa_IR-amir-medium").strip() or "fa_IR-amir-medium"
PIPER_MODELS_DIR = Path(os.getenv("PIPER_MODELS_DIR", str(BASE_DIR / "storage" / "models")))
TTS_CACHE_DIR = Path(os.getenv("TTS_CACHE_DIR", str(BASE_DIR / "storage" / "cache")))

TTS_UNAVAILABLE_MESSAGE = (
    "پاسخ متنی آماده است؛ موتور صوتی آفلاین محلی در دسترس نبود."
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

# ---------------------------------------------------------------------------
# Pending Events Queue (Two-Phase / Event-Driven Architecture)
# ---------------------------------------------------------------------------
PENDING_EVENTS: Dict[str, deque] = {}
_pending_events_lock = threading.Lock()


def add_pending_event(username: str, event: Dict[str, Any]) -> None:
    with _pending_events_lock:
        if username not in PENDING_EVENTS:
            PENDING_EVENTS[username] = deque(maxlen=20)
        PENDING_EVENTS[username].append(event)


def pop_pending_event(username: str) -> Optional[Dict[str, Any]]:
    with _pending_events_lock:
        queue = PENDING_EVENTS.get(username)
        if queue and len(queue) > 0:
            return queue.popleft()
    return None


def peek_all_pending_events(username: str) -> List[Dict[str, Any]]:
    with _pending_events_lock:
        queue = PENDING_EVENTS.get(username)
        if queue:
            return list(queue)
    return []

whisper_model: Optional[WhisperModel] = None
whisper_lock = threading.Lock()

piper_voice: Optional[Any] = None
piper_lock = threading.RLock()
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
            "style-src 'self' 'unsafe-inline'; "
            "font-src 'self' data:; "
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
def _resolve_whisper_model_source() -> str:
    """Finds cached weights on disk to avoid blocking network checks, or falls back to model ID."""
    try:
        hub_dir = Path.home() / ".cache" / "huggingface" / "hub"
        candidates = [
            hub_dir / f"models--mobiuslabsgmbh--faster-whisper-{WHISPER_MODEL_SIZE}",
            hub_dir / f"models--Systran--faster-whisper-{WHISPER_MODEL_SIZE}",
            hub_dir / f"models--dropbox-dash--faster-whisper-{WHISPER_MODEL_SIZE}",
        ]
        for candidate in candidates:
            snapshots_dir = candidate / "snapshots"
            if snapshots_dir.is_dir():
                for snapshot in snapshots_dir.iterdir():
                    model_bin = snapshot / "model.bin"
                    if model_bin.is_file() and model_bin.stat().st_size > 100_000_000:
                        logger.info(
                            f"Verified complete Whisper model cache at {model_bin.parent} "
                            f"({model_bin.stat().st_size / (1024*1024):.1f} MB). Loading directly without remote checks."
                        )
                        return str(snapshot)
    except Exception as exc:
        logger.debug(f"Cache resolution error: {exc}")

    logger.info(
        f"Local cache for '{WHISPER_MODEL_SIZE}' not found or incomplete. "
        "Will download via huggingface_hub with progress logging..."
    )
    return WHISPER_MODEL_SIZE


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
        initial_prompt="سلام، این یک گفت‌وگوی فارسی برای دستیار صوتی هوشمند آوا است.",
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
                        whisper_model = WhisperModel(
                            _resolve_whisper_model_source(),
                            device="cpu",
                            compute_type="int8",
                        )
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
def _sanitize_chat_reply(text: str) -> str:
    if not text:
        return "سلام! روزتون بخیر."
    cliche_regexes = [
        r"[\s،,-]*چطور\s*(?:می‌?تونم|می‌?توانم)\s*(?:بهتون|به\s*شما|کمکتون|کمکتان)?\s*(?:کمک\s*کنم|کمکی\s*بکنم|کمکتون\s*کنم|کمکتان\s*کنم)\s*[؟?]*",
        r"[\s،,-]*چطور\s*(?:کمکتون|کمکتان)\s*کنم\s*[؟?]*",
        r"[\s،,-]*در\s*خدمتم\s*[.!؟?]*",
        r"[\s،,-]*در\s*خدمت\s*(?:شما\s*هستم|تون\s*هستم|تان\s*هستم)\s*[.!؟?]*",
        r"[\s،,-]*چه\s*کمکی\s*(?:از\s*دستم|از\s*دست\s*من|می‌تونم|می‌توانم)\s*(?:برمی‌آید|برمیاد|انجام\s*بدم)?\s*[؟?]*",
        r"[\s،,-]*امری\s*(?:داشتی|داشتید|بفرمایید)\s*[.!؟?]*",
        r"[\s،,-]*امر\s*بفرمایید\s*[.!؟?]*",
    ]
    cleaned = text
    for pat in cliche_regexes:
        cleaned = re.sub(pat, "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip(" ،,-.!?؟")
    if not cleaned:
        original_stripped = text.strip()
        return original_stripped if original_stripped else "سلام!"
    return cleaned


def _build_system_prompt(default_floor: int = 1, pending_context: Optional[str] = None) -> str:
    effective_default = 2 if default_floor == 2 else 1
    ctx = f" (نتیجه اقدام قبلی: {pending_context.strip()})" if pending_context and pending_context.strip() else ""
    return (
        f"تو «آوا» (Ava) هستی؛ دستیار صوتی هوشمند، سریع، صمیمی و دقیق خانه هوشمند (طبقه پیش‌فرض: {effective_default}){ctx}.\n"
        "وظیفه اصلی تو کنترل تجهیزات خانه هوشمند و همراهی صمیمانه با ساکنان خانه است.\n\n"
        "### قوانین الزامی تشخیص قصد (Intent):\n"
        "1. **کنترل تجهیزات (iot_action)**: هرگونه دستور مستقیم، تمایل یا اشاره غیرمستقیم کاربر به تغییر وضعیت یا کنترل تجهیزات خانه "
        "(مانند لامپ و روشنایی، کولر و گرمایش و تنظیم دما، پرده‌ها، پریزها، فن و لوازم برقی) باید بدون استثنا به صورت «iot_action» پردازش شود. "
        "حتی اگر کاربر غیرمستقیم صحبت کرد (مثلاً «خیلی تاریکه»، «هوا گرمه»، «می‌خوام بخوابم»، «صدای تلویزیون زیاده»، «سردم شد»)، آن را معادل تغییر وضعیت تجهیزات مربوطه در نظر بگیر و هرگز به مکالمه عادی نبر!\n"
        f"قالب خروجی iot_action: {{\"intent\": \"iot_action\", \"initial_reply\": \"تایید صوتی گرم، کوتاه و محاوره‌ای به زبان فارسی\", \"ha_action\": {{\"domain\": \"light|switch|climate|cover|fan\", \"service\": \"turn_on|turn_off|open_cover|close_cover|set_temperature\", \"entity_id\": \"domain.device_name\", \"floor\": {effective_default}, \"data\": {{}}}}}}\n\n"
        "2. **گفت‌وگوی روزمره (chat)**: صرفاً زمانی که کاربر سلام و احوال‌پرسی می‌کند، درباره خودت می‌پرسد یا گپ صمیمانه می‌زند و هیچ درخواستی برای تجهیزات خانه ندارد.\n"
        "قالب خروجی chat: {\"intent\": \"chat\", \"reply\": \"پاسخ کوتاه، مهربان و محاوره‌ای به فارسی\"}\n\n"
        "### مثال‌های الگو (Few-Shot Examples):\n"
        f"- کاربر: \"چراغ سالن رو روشن کن\" -> {{\"intent\": \"iot_action\", \"initial_reply\": \"چشم، چراغ سالن روشن شد.\", \"ha_action\": {{\"domain\": \"light\", \"service\": \"turn_on\", \"entity_id\": \"light.living_room\", \"floor\": {effective_default}}}}}\n"
        f"- کاربر: \"اینجا خیلی تاریکه هیچی پیدا نیست\" -> {{\"intent\": \"iot_action\", \"initial_reply\": \"حتماً، الان چراغ‌ها رو برات روشن می‌کنم.\", \"ha_action\": {{\"domain\": \"light\", \"service\": \"turn_on\", \"entity_id\": \"light.living_room\", \"floor\": {effective_default}}}}}\n"
        f"- کاربر: \"کولر رو خاموش کن سردم شد\" -> {{\"intent\": \"iot_action\", \"initial_reply\": \"چشم، کولر خاموش شد.\", \"ha_action\": {{\"domain\": \"climate\", \"service\": \"turn_off\", \"entity_id\": \"climate.ac\", \"floor\": {effective_default}}}}}\n"
        f"- کاربر: \"خیلی گرمه دما رو بذار روی ۲۲\" -> {{\"intent\": \"iot_action\", \"initial_reply\": \"حتماً، دما رو روی ۲۲ درجه گذاشتم.\", \"ha_action\": {{\"domain\": \"climate\", \"service\": \"set_temperature\", \"entity_id\": \"climate.thermostat\", \"floor\": {effective_default}, \"data\": {{\"temperature\": 22}}}}}}\n"
        f"- کاربر: \"پرده‌ها رو بکش آفتاب چشمم رو می‌زنه\" -> {{\"intent\": \"iot_action\", \"initial_reply\": \"چشم، پرده‌ها رو بستم.\", \"ha_action\": {{\"domain\": \"cover\", \"service\": \"close_cover\", \"entity_id\": \"cover.living_room\", \"floor\": {effective_default}}}}}\n"
        f"- کاربر: \"پریز برق قهوه‌ساز رو قطع کن\" -> {{\"intent\": \"iot_action\", \"initial_reply\": \"چشم، پریز قهوه‌ساز خاموش شد.\", \"ha_action\": {{\"domain\": \"switch\", \"service\": \"turn_off\", \"entity_id\": \"switch.coffee_maker\", \"floor\": {effective_default}}}}}\n"
        f"- کاربر: \"سلام آوا روزت بخیر چطوری؟\" -> {{\"intent\": \"chat\", \"reply\": \"سلام عزیزم! من عالی‌ام، در خدمتم هر کاری داشتی بگو.\"}}\n\n"
        "قانون خروجی: فقط و فقط یک آبجکت معتبر JSON خروجی بده بدون هیچ توضیح یا متن اضافه."
    )


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


def _call_ollama(**kwargs: Any) -> Any:
    def _invoke(chat_fn: Any) -> Any:
        call_kwargs = dict(kwargs)
        # Ensure "think": False is strictly top-level in the payload, never inside options
        if isinstance(call_kwargs.get("options"), dict):
            call_kwargs["options"].pop("think", None)
        call_kwargs["think"] = False

        if "keep_alive" not in call_kwargs:
            call_kwargs["keep_alive"] = "24h"
        elif call_kwargs.get("keep_alive") == "-1":
            call_kwargs["keep_alive"] = -1
        try:
            return chat_fn(**call_kwargs)
        except TypeError:
            call_kwargs.pop("think", None)
            return chat_fn(**call_kwargs)
        except ollama.ResponseError as exc:
            err_text = f"{exc} {getattr(exc, 'error', '')}".lower()
            if "think" in err_text:
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


_call_ollama_chat = _call_ollama


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
    # Check for intent-based or conversational candidates
    for candidate in candidates:
        if isinstance(candidate, dict):
            if candidate.get("intent") in ("chat", "iot_action"):
                return candidate
            if candidate.get("type") == "conversation" or "reply" in candidate:
                if candidate.get("reply"):
                    return candidate

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


def reason_with_qwen(
    transcript: str,
    default_floor: int = 1,
    pending_context: Optional[str] = None,
) -> Dict[str, Any]:
    effective_default = 2 if default_floor == 2 else 1
    system_prompt = _build_system_prompt(default_floor=effective_default, pending_context=pending_context)

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

    user_prompt = transcript.strip()
    if not user_prompt.endswith("/no_think"):
        user_prompt = f"{user_prompt} /no_think"

    logger.info(f"Querying Ollama model '{active_model}' (default_floor={effective_default})...")
    try:
        response = _call_ollama(
            model=active_model,
            format="json",
            keep_alive="24h",
            think=False,
            options={
                "temperature": 0.6,
                "num_ctx": 2048,
                "num_predict": 128,
            },
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
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
        intent = parsed_dict.get("intent")

        # 1. Chat Branch (greetings, general chat, or reply-only)
        if (
            intent == "chat"
            or parsed_dict.get("type") == "conversation"
            or ("reply" in parsed_dict and "ha_action" not in parsed_dict and "domain" not in parsed_dict)
        ):
            raw_reply = str(
                parsed_dict.get("reply") or parsed_dict.get("initial_reply") or ""
            ).strip()
            reply_text = _sanitize_chat_reply(raw_reply) if raw_reply else "سلام!"
            return {
                "intent": "chat",
                "reply": reply_text,
                "model_used": active_model,
            }

        # 2. IoT Action Branch
        ha_payload = parsed_dict.get("ha_action")
        if not isinstance(ha_payload, dict):
            ha_payload = parsed_dict

        floor_val = _normalize_floor(ha_payload.get("floor"), default_floor=effective_default)
        domain_val = str(ha_payload.get("domain") or "light").strip()
        service_val = str(ha_payload.get("service") or "turn_on").strip()
        entity_id_val = str(ha_payload.get("entity_id") or f"{domain_val}.living_room").strip()
        data_val = ha_payload.get("data") if "data" in ha_payload else ha_payload.get("service_data", {})
        if not isinstance(data_val, dict):
            data_val = {}

        initial_reply_text = str(
            parsed_dict.get("initial_reply")
            or parsed_dict.get("ack_message")
            or "چشم، الان تنظیمش می‌کنم."
        ).strip()

        success_msg_text = str(
            parsed_dict.get("success_message")
            or f"{entity_id_val} تنظیم شد."
        ).strip()

        command = LLMCommandSchema(
            floor=floor_val,
            domain=domain_val,
            service=service_val,
            entity_id=entity_id_val,
            service_data=data_val,
            ack_message=initial_reply_text,
            success_message=success_msg_text,
        )
        command._model_used = active_model

        return {
            "intent": "iot_action",
            "initial_reply": initial_reply_text,
            "command_data": command,
            "ha_action": {
                "floor": floor_val,
                "domain": domain_val,
                "service": service_val,
                "entity_id": entity_id_val,
                "data": data_val,
            },
            "model_used": active_model,
        }
    except (ValueError, json.JSONDecodeError, ValidationError, TypeError) as parse_exc:
        logger.exception(f"Failed to parse LLM JSON output ({active_model}): {parse_exc}")
        raise OllamaServiceError(
            detail=f"خروجی مدل زبانی ({active_model}) ساختار معتبر JSON نداشت.",
            status_code=502,
            error_code="LLM_INVALID_JSON",
            ollama_running=True,
            requested_model=active_model,
            available_models=available_models,
        ) from parse_exc


def _format_ha_success_report(command_data: LLMCommandSchema, ha_result: Dict[str, Any]) -> str:
    domain = command_data.domain
    service = command_data.service
    entity = command_data.entity_id
    floor = command_data.floor
    floor_str = f"طبقه {floor}"

    name_map = {
        "light.living_room": "چراغ پذیرایی",
        "light.kitchen": "چراغ آشپزخانه",
        "light.bedroom": "چراغ اتاق خواب",
        "light.hallway": "چراغ راهرو",
        "switch.fridge_express_freeze": "انجماد سریع یخچال",
        "switch.water_heater": "پکیج آبگرمکن",
        "switch.coffee_maker": "قهوه‌ساز",
        "climate.main_thermostat": "ترموستات",
    }
    entity_fa = name_map.get(entity, entity.split(".")[-1].replace("_", " "))

    if service in ("turn_on", "on"):
        return f"{entity_fa} {floor_str} روشن شد."
    if service in ("turn_off", "off"):
        return f"{entity_fa} {floor_str} خاموش شد."
    if service in ("toggle",):
        return f"وضعیت {entity_fa} {floor_str} تغییر کرد."
    if service in ("set_temperature",):
        temp = (command_data.service_data or {}).get("temperature")
        if temp is not None:
            return f"دمای {entity_fa} روی {temp} تنظیم شد."
        return f"دمای {entity_fa} {floor_str} تنظیم شد."

    if command_data.success_message and len(command_data.success_message.split()) <= 10:
        return command_data.success_message
    return f"فرمان {entity_fa} {floor_str} با موفقیت اجرا شد."


async def _run_ha_background_action(
    username: str,
    command_data: LLMCommandSchema,
    allowed_floors: Set[int],
) -> None:
    try:
        ha_result = await asyncio.to_thread(
            execute_home_assistant_service, command_data, allowed_floors
        )
        report_msg = _format_ha_success_report(command_data, ha_result)
        add_pending_event(
            username,
            {
                "status": "success",
                "message": report_msg,
                "entity_id": command_data.entity_id,
                "floor": command_data.floor,
                "timestamp": time.time(),
            },
        )
        logger.info(f"Background HA action completed for '{username}': {report_msg}")
    except PermissionError:
        err_msg = f"دسترسی به طبقه {command_data.floor} مجاز نیست."
        add_pending_event(
            username,
            {
                "status": "error",
                "message": err_msg,
                "entity_id": command_data.entity_id,
                "floor": command_data.floor,
                "timestamp": time.time(),
            },
        )
    except HAServiceError as ha_err:
        short_err = f"خطا در طبقه {command_data.floor}: {ha_err.detail}"
        if len(short_err.split()) > 10:
            short_err = f"خطا در ارتباط با طبقه {command_data.floor}."
        add_pending_event(
            username,
            {
                "status": "error",
                "message": short_err,
                "entity_id": command_data.entity_id,
                "floor": command_data.floor,
                "timestamp": time.time(),
            },
        )
    except Exception as exc:
        logger.exception(f"Unexpected background HA error for '{username}': {exc}")
        add_pending_event(
            username,
            {
                "status": "error",
                "message": "خطای غیرمنتظره در ارتباط با دستگاه.",
                "entity_id": command_data.entity_id,
                "floor": command_data.floor,
                "timestamp": time.time(),
            },
        )


# ---------------------------------------------------------------------------
# 100% Offline Local Piper TTS Engine & Disk Pre-Cache
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
                "Offline Piper voice will remain standby until .onnx + .onnx.json files are present."
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
        logger.error("Piper voice is unavailable; cannot synthesize speech.")
        return False
    wav_output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with piper_lock:
            with wave.open(str(wav_output_path), "wb") as wav_file:
                voice.synthesize_wav(text, wav_file)
        return wav_output_path.exists() and wav_output_path.stat().st_size > 44
    except Exception as exc:
        logger.exception(f"Error during Piper speech synthesis: {exc}")
        return False


def _get_cached_phrase_path(text: str) -> Optional[Path]:
    """Returns a cached .wav or .mp3 file from TTS_CACHE_DIR if text matches a pre-cached phrase."""
    text_hash = hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:24]
    for ext in (".wav", ".mp3"):
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
    return TEMP_AUDIO_DIR / f"latest_response_{safe_user}.wav", TEMP_AUDIO_DIR / f"latest_response_{safe_user}.mp3"


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
    for pattern in ("response_*.wav", "response_*.mp3"):
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
        return "TTS timeout"
    return f"TTS error ({type(exc).__name__})"


async def synthesize_persian_tts(
    text: str,
    output_stem_path: Path,
    allow_cache_hit: bool = True,
) -> Dict[str, Any]:
    """
    100% Offline Local Persian TTS using Piper (fa_IR-amir-medium).
    1. Instant disk cache hit for pre-cached common phrases (.wav).
    2. Direct local synthesis using Piper TTS -> .wav
    """
    cleaned_text = (text or "").strip()
    if not cleaned_text:
        return {
            "ok": False,
            "engine": "none",
            "voice": PIPER_MODEL_NAME,
            "path": None,
            "fallback_message": TTS_UNAVAILABLE_MESSAGE,
            "error": "Empty TTS input text",
        }

    # 1. Check pre-cached disk audio (.wav or .mp3)
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

    # 2. 100% Offline Local Piper TTS (.wav)
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
        logger.warning(f"Piper TTS synthesis failed for: {cleaned_text}")
    except Exception as piper_exc:
        logger.exception(f"Piper offline TTS failed: {piper_exc}")
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
        "error": "Piper voice unavailable or synthesis failed",
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
        wav_latest, mp3_latest = _get_user_latest_audio_paths(username)
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
    """Pre-caches common Persian error responses on disk using 100% offline local Piper TTS."""
    TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for key, phrase in COMMON_ERROR_TTS_PHRASES.items():
        if _get_cached_phrase_path(phrase) is not None:
            continue
        text_hash = hashlib.sha256(phrase.strip().encode("utf-8")).hexdigest()[:24]
        stem = TTS_CACHE_DIR / f"err_{text_hash}"
        wav_candidate = stem.with_suffix(".wav")
        try:
            piper_ok = await asyncio.to_thread(_synthesize_with_piper_sync, phrase, wav_candidate)
            if piper_ok:
                logger.info(f"Pre-cached error TTS '{key}' via local Piper -> {wav_candidate.name}")
        except Exception as exc:
            logger.warning(f"Failed to pre-cache error TTS '{key}' via Piper: {exc}")


async def _warmup_ollama_background() -> None:
    try:
        logger.info(f"Warming up Ollama model '{OLLAMA_MODEL}' in background (keep_alive='24h')...")
        await asyncio.to_thread(
            _call_ollama,
            model=OLLAMA_MODEL,
            messages=[{"role": "user", "content": "سلام /no_think"}],
            keep_alive="24h",
            think=False,
            options={
                "temperature": 0.6,
                "num_ctx": 2048,
                "num_predict": 128,
            },
        )
        logger.info(f"Ollama model '{OLLAMA_MODEL}' warmed up and pinned in memory.")
    except Exception as exc:
        logger.warning(f"Ollama warm-up warning (will retry on first request): {exc}")


# ---------------------------------------------------------------------------
# FastAPI App & Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    global whisper_model
    ensure_users_file()
    validate_startup_auth_config()

    try:
        import huggingface_hub.utils.logging as hf_logging
        hf_logging.set_verbosity_info()
    except Exception:
        pass

    model_source = _resolve_whisper_model_source()
    logger.info(f"Loading Faster-Whisper model from source '{model_source}' on GPU (device='cuda', compute_type='int8_float16')...")
    try:
        whisper_model = await asyncio.to_thread(
            lambda: WhisperModel(
                model_source,
                device="cuda",
                compute_type="int8_float16",
            )
        )
        logger.info(f"Faster-Whisper ('{WHISPER_MODEL_SIZE}') loaded successfully on CUDA (int8_float16).")
    except Exception as exc:
        logger.warning(f"CUDA Whisper initialization failed ({exc}); falling back to CPU (int8)...")
        try:
            whisper_model = await asyncio.to_thread(
                lambda: WhisperModel(
                    model_source,
                    device="cpu",
                    compute_type="int8",
                )
            )
            logger.info("Faster-Whisper loaded on CPU.")
        except Exception as cpu_err:
            logger.exception(f"Faster-Whisper CPU initialization failed: {cpu_err}")
            whisper_model = None

    # Preload Piper model & pre-cache common error responses in background
    await asyncio.to_thread(_get_piper_voice)
    asyncio.create_task(_prewarm_common_error_tts_cache())

    # Warm-up Ollama model in background so server startup is instantaneous
    asyncio.create_task(_warmup_ollama_background())

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
            ("GET", "/api/health"),
            ("HEAD", "/api/health"),
            ("GET", "/health"),
            ("HEAD", "/health"),
            ("GET", "/api/gateway-info"),
            ("HEAD", "/api/gateway-info"),
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
@app.get("/api/health")
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "smart-building-voice-gateway",
        "version": "2.0.0",
        "timestamp": time.time(),
    }


@app.get("/api/gateway-info")
@app.get("/gateway-info", include_in_schema=False)
async def get_gateway_info():
    tunnel_file = BASE_DIR / "scripts" / "tunnel_url.txt"
    remote_url = ""
    if tunnel_file.exists():
        try:
            content = tunnel_file.read_text(encoding="utf-8").strip()
            if content.startswith("http://") or content.startswith("https://"):
                remote_url = content
        except Exception:
            pass
    if not remote_url:
        remote_url = "https://tribune-baghdad-mandatory-handy.trycloudflare.com"

    return {
        "remote_url": remote_url,
        "local_url": "http://192.168.137.1:8000",
        "service": "smart-building-voice-gateway",
        "version": "2.0.0",
        "timestamp": time.time(),
    }


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
    # Support logging in with username or email (e.g. ilia, floor1_user, floor1@building.local)
    identifier = payload.username.strip()
    user_record = registry.get(identifier)
    if user_record is None:
        user_record = await asyncio.to_thread(get_user_by_email, identifier)

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
        transcript = ""
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
                transcript=transcript or None,
            )

        # Check hands_free mode flag from request form_data
        raw_hands_free = form_data.get("hands_free") if form_data is not None else None
        is_hands_free = str(raw_hands_free or "").strip().lower() in ("true", "1", "yes")

        if not transcript:
            if is_hands_free:
                logger.info("Hands-free audio ignored: empty transcript / background noise.")
                return JSONResponse(
                    status_code=200,
                    content={"status": "ignored", "ignored": True, "reason": "empty_transcript"},
                    headers=NO_STORE_HEADERS,
                )
            msg = COMMON_ERROR_TTS_PHRASES["empty_transcript"]
            err_audio_url, _ = await _prepare_user_audio_response(msg, auth_user.username)
            return build_error_response(
                status_code=422,
                detail=msg,
                stage="stt",
                error_code="EMPTY_TRANSCRIPT",
                audio_url=err_audio_url,
                transcript=None,
            )

        # Hands-free Wake Word / Smart Intent Guard
        if is_hands_free:
            transcript_clean = transcript.strip().lower()
            wake_words = ("آوا", "هی آوا", "سلام آوا", "ایوا", "ava", "hey ava")
            has_wake_word = any(w in transcript_clean for w in wake_words)
            iot_indicators = (
                "روشن", "خاموش", "بزن", "قطع", "وصل", "دما", "کولر", "چراغ",
                "لامپ", "پرده", "پریز", "اسپلیت", "هواکش", "فن", "قفل", "باز کن", "ببند"
            )
            has_iot_intent = any(w in transcript_clean for w in iot_indicators)
            if not has_wake_word and not has_iot_intent:
                logger.info(f"Hands-free audio ignored (no wake word or IoT command): '{transcript}'")
                return JSONResponse(
                    status_code=200,
                    content={"status": "ignored", "ignored": True, "transcript": transcript},
                    headers=NO_STORE_HEADERS,
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
                transcript=transcript,
            )

        # 3. Context Injection from Pending Events Queue
        pending_event = pop_pending_event(auth_user.username)
        pending_context = None
        if pending_event:
            pending_context = str(pending_event.get("message") or "").strip()
            logger.info(f"Injecting pending context for '{auth_user.username}': {sanitize_for_log(pending_context, 100)}")

        # 4. Ollama Reasoning (qwen3.5:4b) with Intent Routing & Context Injection
        current_stage = "llm"
        try:
            decision = await asyncio.to_thread(
                reason_with_qwen, transcript, auth_user.default_floor, pending_context
            )
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
                transcript=transcript,
            )

        intent = decision.get("intent", "chat")
        model_used = decision.get("model_used") or OLLAMA_MODEL

        # 5. Branch: General Conversation (Chat)
        if intent == "chat":
            current_stage = "tts"
            raw_reply = str(decision.get("reply") or decision.get("initial_reply") or "").strip()
            reply_text = _sanitize_chat_reply(raw_reply) if raw_reply else "سلام!"
            audio_url, tts_status = await _prepare_user_audio_response(reply_text, auth_user.username)
            return JSONResponse(
                status_code=200,
                content={
                    "status": "success",
                    "intent": "chat",
                    "transcript": transcript,
                    "reply": reply_text,
                    "parsed_command": {
                        "type": "conversation",
                        "reply": reply_text,
                        "domain": "conversation",
                        "service": "reply",
                        "entity_id": "assistant",
                        "floor": None,
                        "service_data": {},
                        "ack_message": reply_text,
                        "success_message": reply_text,
                        "_is_conversation": True,
                    },
                    "llm_model": model_used,
                    "ha_execution": {"ok": True, "type": "chat", "status_code": 200},
                    "audio_url": audio_url,
                    "tts_status": tts_status,
                },
                headers=NO_STORE_HEADERS,
            )

        # 6. Branch: Smart Home IoT Action (Two-Phase Parallel Execution)
        command_data: LLMCommandSchema = decision["command_data"]
        initial_reply = str(decision.get("initial_reply") or "چشم، الان تنظیمش می‌کنم.").strip()

        # Post-LLM Floor Guard
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
                transcript=transcript,
            )

        # Immediate Audio Synthesis of initial_reply (<10-12 words)
        current_stage = "tts"
        audio_url, tts_status = await _prepare_user_audio_response(initial_reply, auth_user.username)

        # Parallel Async Home Assistant Execution in Background Task
        asyncio.create_task(
            _run_ha_background_action(
                username=auth_user.username,
                command_data=command_data,
                allowed_floors=set(auth_user.allowed_floors),
            )
        )

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "intent": "iot_action",
                "transcript": transcript,
                "initial_reply": initial_reply,
                "parsed_command": command_data.model_dump(),
                "llm_model": model_used,
                "ha_execution": {
                    "ok": True,
                    "status": "pending",
                    "message": "فرمان جهت اجرا در پس‌زمینه ارسال شد.",
                },
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
            transcript=transcript or None,
        )
    except Exception as exc:
        logger.exception(f"Unexpected error in /process-audio at stage '{current_stage}': {exc}")
        return build_error_response(
            status_code=500,
            detail=f"خطای غیرمنتظره در پردازش فرمان صوتی (مرحله {current_stage}).",
            stage=current_stage,
            error_code="PROCESS_AUDIO_INTERNAL_ERROR",
            transcript=transcript or None,
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
    """Streams owner-isolated WAV (100% offline Piper TTS) or cached audio response back to client."""
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
        for ext in (".wav", ".mp3"):
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


@app.get("/api/pending-event")
async def get_pending_event(auth_user: AuthUser = Depends(require_authenticated_user)):
    """Polls pending Home Assistant background execution results for the user and synthesizes voice."""
    event = pop_pending_event(auth_user.username)
    if not event:
        return JSONResponse(status_code=200, content={"has_event": False}, headers=NO_STORE_HEADERS)

    message_text = str(event.get("message") or "عملیات انجام شد.").strip()
    audio_url, tts_status = await _prepare_user_audio_response(message_text, auth_user.username)

    return JSONResponse(
        status_code=200,
        content={
            "has_event": True,
            "status": event.get("status", "success"),
            "message": message_text,
            "entity_id": event.get("entity_id"),
            "floor": event.get("floor"),
            "audio_url": audio_url,
            "tts_status": tts_status,
            "timestamp": event.get("timestamp", time.time()),
        },
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
    uvicorn.run("app.main:app", host=host, port=chosen_port, reload=False)
