import base64
import hashlib
import hmac
import json
import logging
import math
import os
import re
import secrets
import smtplib
import tempfile
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Set, Tuple, Union

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("SmartBuildingVoiceGateway.Auth")

# ---------------------------------------------------------------------------
# Constants & Configuration
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
USERS_FILE = Path(os.getenv("USERS_FILE", str(BASE_DIR / "storage" / "users.json")))

JWT_ISSUER = "smart-building-voice-gateway"
MAX_JWT_LENGTH = 2048
DEFAULT_TOKEN_TTL_SECONDS = 43200
MIN_TOKEN_TTL_SECONDS = 60
MAX_TOKEN_TTL_SECONDS = 604800
DEFAULT_PBKDF2_ITERATIONS = 600000
MIN_PROD_PBKDF2_ITERATIONS = 310000
MAX_PBKDF2_ITERATIONS = 10000000
DEFAULT_LOGIN_MAX_ATTEMPTS = 10
DEFAULT_LOGIN_WINDOW_SECONDS = 60
MAX_RATE_LIMIT_IPS = 10000

OTP_TTL_SECONDS = 300  # 5 minutes
OTP_MAX_ATTEMPTS = 5
MIN_PASSWORD_LENGTH = 6
MAX_PASSWORD_LENGTH = 256

_EPHEMERAL_JWT_SECRET = secrets.token_urlsafe(32)
_DUMMY_PBKDF2_SALT = secrets.token_bytes(16)
NO_STORE_HEADERS = {"Cache-Control": "no-store, private", "Pragma": "no-cache"}

# Default PBKDF2 hashes used only to seed users.json on first initialization
_SEED_DEFAULT_HASHES: Dict[str, str] = {
    "floor1_user": "pbkdf2_sha256$600000$7f12bde866b48b5b3c7b995ea663523c$9ad7671ea8b9d2c39bed501b391ce2c4d7af0f5a53a1cf63e863a64185b12ffc",
    "floor2_user": "pbkdf2_sha256$600000$0db3a6ec6f79d495d3ff87c316d8d969$56cc809e37f53c1c16b33c98084aae8e51d919a2ae8c1e1aeed3af08975f355b",
    "admin": "pbkdf2_sha256$600000$ea7a86b6af1262efbf940c71313e86c6$fe58b0556f3fee2a246a76fa4e9534d35a1c73553eeff9b838a4f12c706d55e3",
}

_SEED_USER_SPECS = (
    {
        "username": "floor1_user",
        "email_env": "FLOOR1_USER_EMAIL",
        "default_email": "floor1@building.local",
        "role": "resident",
        "floor": 1,
        "default_floor": 1,
        "allowed_floors": [1],
        "hash_envs": ("AUTH_FLOOR1_PASSWORD_HASH", "FLOOR1_USER_PASSWORD_HASH"),
        "plain_envs": ("AUTH_FLOOR1_PASSWORD", "FLOOR1_USER_PASSWORD"),
    },
    {
        "username": "floor2_user",
        "email_env": "FLOOR2_USER_EMAIL",
        "default_email": "floor2@building.local",
        "role": "resident",
        "floor": 2,
        "default_floor": 2,
        "allowed_floors": [2],
        "hash_envs": ("AUTH_FLOOR2_PASSWORD_HASH", "FLOOR2_USER_PASSWORD_HASH"),
        "plain_envs": ("AUTH_FLOOR2_PASSWORD", "FLOOR2_USER_PASSWORD"),
    },
    {
        "username": "admin",
        "email_env": "ADMIN_USER_EMAIL",
        "default_email": "admin@building.local",
        "role": "admin",
        "floor": "all",
        "default_floor": 1,
        "allowed_floors": [1, 2],
        "hash_envs": ("AUTH_ADMIN_PASSWORD_HASH", "ADMIN_USER_PASSWORD_HASH"),
        "plain_envs": ("AUTH_ADMIN_PASSWORD", "ADMIN_USER_PASSWORD"),
    },
)

_TRIVIAL_SECRET_DENYLIST: Set[str] = {
    "secret",
    "changeme",
    "password",
    "your-secret-key",
    "jwt_secret",
    "default",
    "dev",
    "test",
}


# ---------------------------------------------------------------------------
# Exceptions & Error Payload Helpers
# ---------------------------------------------------------------------------
class GatewayAuthError(Exception):
    """Raised when authentication, token validation, or floor/audio authorization fails."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        error_code: str = "UNAUTHORIZED",
        stage: str = "auth",
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.error_code = error_code
        self.stage = stage
        self.headers = dict(headers) if headers else None


def sanitize_for_log(text: Any, max_len: int = 500) -> str:
    """Strips CR/LF and control characters from untrusted values before logging."""
    cleaned = re.sub(r"[\r\n\x00-\x1f\x7f]+", " ", str(text or "")).strip()
    if len(cleaned) > max_len:
        return f"{cleaned[:max_len]}..."
    return cleaned


def build_error_payload(
    detail: str,
    stage: str = "internal",
    error_code: str = "INTERNAL_ERROR",
    ollama_running: Optional[bool] = None,
    requested_model: Optional[str] = None,
    available_models: Optional[List[str]] = None,
    audio_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Builds a consistent JSON error envelope for all endpoints and exception handlers."""
    payload: Dict[str, Any] = {
        "status": "error",
        "detail": detail,
        "stage": stage,
        "error_code": error_code,
        "ollama_running": ollama_running,
        "requested_model": requested_model,
        "available_models": list(available_models) if available_models is not None else [],
    }
    if audio_url is not None:
        payload["audio_url"] = audio_url
    return payload


def build_error_response(
    status_code: int,
    detail: str,
    stage: str = "internal",
    error_code: str = "INTERNAL_ERROR",
    ollama_running: Optional[bool] = None,
    requested_model: Optional[str] = None,
    available_models: Optional[List[str]] = None,
    audio_url: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
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
            audio_url=audio_url,
        ),
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AuthUser:
    username: str
    role: Literal["resident", "admin"]
    floor: Union[Literal[1, 2], Literal["all"]]
    default_floor: Literal[1, 2]
    allowed_floors: Tuple[int, ...]
    auth_method: Literal["jwt", "api_token"]
    expires_at: Optional[int] = None
    email: str = ""


@dataclass(frozen=True)
class UserRecord:
    username: str
    email: str
    role: Literal["resident", "admin"]
    floor: Union[Literal[1, 2], Literal["all"]]
    default_floor: Literal[1, 2]
    allowed_floors: Tuple[int, ...]
    password_hash: Optional[str]
    active: bool


@dataclass
class _OTPEntry:
    username: str
    email: str
    otp_hash: str
    expires_at: float
    attempts: int = 0


# ---------------------------------------------------------------------------
# Thread-Safe State
# ---------------------------------------------------------------------------
_users_file_lock = threading.RLock()
_cached_users_mtime_ns: Optional[int] = None
_cached_users_path: Optional[Path] = None
_cached_users_by_name: Dict[str, UserRecord] = {}

_login_rate_lock = threading.Lock()
_login_failures_by_ip: "OrderedDict[str, deque[float]]" = OrderedDict()

_otp_lock = threading.Lock()
_otp_store: Dict[str, _OTPEntry] = {}


# ---------------------------------------------------------------------------
# Environment & Secret Helpers
# ---------------------------------------------------------------------------
def get_app_env() -> str:
    return os.getenv("APP_ENV", "development").strip().lower() or "development"


def is_production_env() -> bool:
    return get_app_env() in {"production", "prod"}


def _dev_defaults_enabled() -> bool:
    if is_production_env():
        return False
    return os.getenv("AUTH_ALLOW_DEV_DEFAULTS", "true").strip().lower() != "false"


def _parse_bounded_int_env(name: str, default: int, min_val: int, max_val: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        logger.warning(f"Invalid integer value for {name}; using default {default}.")
        return default
    return max(min_val, min(max_val, parsed))


def get_token_ttl_seconds() -> int:
    return _parse_bounded_int_env(
        "AUTH_TOKEN_TTL_SECONDS",
        DEFAULT_TOKEN_TTL_SECONDS,
        MIN_TOKEN_TTL_SECONDS,
        MAX_TOKEN_TTL_SECONDS,
    )


def get_pbkdf2_iterations() -> int:
    min_iters = MIN_PROD_PBKDF2_ITERATIONS if is_production_env() else 1
    return _parse_bounded_int_env(
        "AUTH_PBKDF2_ITERATIONS",
        DEFAULT_PBKDF2_ITERATIONS,
        min_iters,
        MAX_PBKDF2_ITERATIONS,
    )


def _get_login_max_attempts() -> int:
    return _parse_bounded_int_env("AUTH_LOGIN_MAX_ATTEMPTS", DEFAULT_LOGIN_MAX_ATTEMPTS, 0, 10000)


def _get_login_window_seconds() -> int:
    return _parse_bounded_int_env("AUTH_LOGIN_WINDOW_SECONDS", DEFAULT_LOGIN_WINDOW_SECONDS, 1, 86400)


def get_voice_api_token() -> str:
    return os.getenv("VOICE_API_TOKEN", "").strip()


def _is_weak_secret(secret: str) -> bool:
    if len(secret) < 32:
        return True
    if secret.strip().lower() in _TRIVIAL_SECRET_DENYLIST:
        return True
    if len(set(secret)) < 8:
        return True
    secret_bytes = secret.encode("utf-8")
    voice_tok = get_voice_api_token()
    if voice_tok and hmac.compare_digest(secret_bytes, voice_tok.encode("utf-8")):
        return True
    for env_key in ("HA_TOKEN_FLOOR1", "HA_FLOOR1_TOKEN", "HA_TOKEN_FLOOR2", "HA_FLOOR2_TOKEN"):
        ha_tok = os.getenv(env_key, "").strip()
        if ha_tok and hmac.compare_digest(secret_bytes, ha_tok.encode("utf-8")):
            return True
    return False


def get_jwt_secret_bytes() -> bytes:
    env_secret = os.getenv("AUTH_JWT_SECRET", "").strip() or os.getenv("JWT_SECRET", "").strip()
    if is_production_env():
        if not env_secret or _is_weak_secret(env_secret):
            raise RuntimeError(
                "AUTH_JWT_SECRET must be set to at least 32 high-entropy characters in production."
            )
        return env_secret.encode("utf-8")
    if env_secret:
        if _is_weak_secret(env_secret):
            raise RuntimeError(
                "Configured AUTH_JWT_SECRET/JWT_SECRET is too weak (<32 chars, trivial, or reused)."
            )
        return env_secret.encode("utf-8")
    return _EPHEMERAL_JWT_SECRET.encode("utf-8")


# ---------------------------------------------------------------------------
# PBKDF2 Password Hashing
# ---------------------------------------------------------------------------
def hash_password_pbkdf2(password: str, iterations: Optional[int] = None) -> str:
    iters = get_pbkdf2_iterations() if iterations is None else iterations
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters)
    return f"pbkdf2_sha256${iters}${salt.hex()}${digest.hex()}"


def parse_pbkdf2_hash(stored_hash: Optional[str], min_iterations: int = 1) -> Optional[Tuple[int, bytes, bytes]]:
    if not isinstance(stored_hash, str):
        return None
    parts = stored_hash.strip().split("$")
    if len(parts) != 4:
        return None
    alg, iters_str, salt_hex, digest_hex = parts
    if alg != "pbkdf2_sha256" or not iters_str.isdigit():
        return None
    iters = int(iters_str)
    if not (min_iterations <= iters <= MAX_PBKDF2_ITERATIONS):
        return None
    try:
        salt = bytes.fromhex(salt_hex)
        digest = bytes.fromhex(digest_hex)
    except ValueError:
        return None
    if len(salt) < 16 or len(digest) != 32:
        return None
    return iters, salt, digest


def verify_password_pbkdf2(password: str, stored_hash: Optional[str]) -> bool:
    min_iters = MIN_PROD_PBKDF2_ITERATIONS if is_production_env() else 1
    parsed = parse_pbkdf2_hash(stored_hash, min_iterations=min_iters)
    pw_bytes = password.encode("utf-8")
    if parsed is None:
        hashlib.pbkdf2_hmac("sha256", pw_bytes, _DUMMY_PBKDF2_SALT, get_pbkdf2_iterations())
        return False
    iters, salt, expected_digest = parsed
    computed_digest = hashlib.pbkdf2_hmac("sha256", pw_bytes, salt, iters)
    return hmac.compare_digest(computed_digest, expected_digest)


# ---------------------------------------------------------------------------
# Dynamic users.json Persistence & Registry
# ---------------------------------------------------------------------------
def _get_first_nonempty_env(names: Tuple[str, ...]) -> str:
    for name in names:
        val = os.getenv(name, "")
        if val != "" and val.strip() != "":
            return val
    return ""


def _build_seed_users_payload() -> List[Dict[str, Any]]:
    is_prod = is_production_env()
    allow_dev = _dev_defaults_enabled()
    iterations = get_pbkdf2_iterations()
    min_iters = MIN_PROD_PBKDF2_ITERATIONS if is_prod else 1

    seeded: List[Dict[str, Any]] = []
    for spec in _SEED_USER_SPECS:
        uname = spec["username"]
        hash_val = _get_first_nonempty_env(spec["hash_envs"]).strip()
        plain_val = _get_first_nonempty_env(spec["plain_envs"])
        password_hash: Optional[str] = None
        active = False

        if hash_val and parse_pbkdf2_hash(hash_val, min_iterations=min_iters) is not None:
            password_hash = hash_val
            active = True
        elif plain_val:
            password_hash = hash_password_pbkdf2(plain_val, iterations=iterations)
            active = True
        elif allow_dev:
            password_hash = _SEED_DEFAULT_HASHES[uname]
            active = True

        email = os.getenv(spec["email_env"], spec["default_email"]).strip() or spec["default_email"]
        seeded.append(
            {
                "username": uname,
                "email": email,
                "role": spec["role"],
                "floor": spec["floor"],
                "default_floor": spec["default_floor"],
                "allowed_floors": list(spec["allowed_floors"]),
                "password_hash": password_hash,
                "active": active,
            }
        )
    return seeded


def _write_users_payload_atomic(users_list: List[Dict[str, Any]], target_path: Optional[Path] = None) -> None:
    path = USERS_FILE if target_path is None else target_path
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps({"users": users_list}, ensure_ascii=False, indent=2)
    fd, tmp_name = tempfile.mkstemp(prefix=".users_", suffix=".json.tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(serialized)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            Path(tmp_name).unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _parse_user_dict(item: Dict[str, Any]) -> Optional[UserRecord]:
    username = str(item.get("username") or "").strip()
    if not username:
        return None
    email = str(item.get("email") or "").strip()
    role_raw = str(item.get("role") or "resident").strip().lower()
    role: Literal["resident", "admin"] = "admin" if role_raw == "admin" else "resident"

    raw_floor = item.get("floor")
    if role == "admin" or str(raw_floor).strip().lower() == "all":
        floor: Union[Literal[1, 2], Literal["all"]] = "all"
        allowed_floors_raw = item.get("allowed_floors") or [1, 2]
    else:
        floor_int = 2 if str(raw_floor).strip() == "2" or raw_floor == 2 else 1
        floor = floor_int
        allowed_floors_raw = item.get("allowed_floors") or [floor_int]

    allowed_clean = sorted({int(f) for f in allowed_floors_raw if int(f) in (1, 2)})
    if not allowed_clean:
        allowed_clean = [1, 2] if floor == "all" else [int(floor)]

    raw_default_floor = item.get("default_floor", allowed_clean[0])
    default_floor: Literal[1, 2] = 2 if raw_default_floor == 2 and 2 in allowed_clean else (
        1 if 1 in allowed_clean else 2
    )

    password_hash = item.get("password_hash")
    if password_hash is not None:
        password_hash = str(password_hash).strip() or None

    active = bool(item.get("active", True)) and bool(password_hash)
    return UserRecord(
        username=username,
        email=email,
        role=role,
        floor=floor,
        default_floor=default_floor,
        allowed_floors=tuple(allowed_clean),
        password_hash=password_hash,
        active=active,
    )


def _record_to_dict(rec: UserRecord) -> Dict[str, Any]:
    return {
        "username": rec.username,
        "email": rec.email,
        "role": rec.role,
        "floor": rec.floor,
        "default_floor": rec.default_floor,
        "allowed_floors": list(rec.allowed_floors),
        "password_hash": rec.password_hash,
        "active": rec.active,
    }


def ensure_users_file() -> Path:
    """Ensures users.json exists on disk, seeding it on first run if absent."""
    with _users_file_lock:
        if not USERS_FILE.exists():
            seed_users = _build_seed_users_payload()
            _write_users_payload_atomic(seed_users, USERS_FILE)
            logger.info(f"Initialized local user store at '{USERS_FILE.name}' with {len(seed_users)} users.")
    return USERS_FILE


def get_user_registry(force_reload: bool = False) -> Dict[str, UserRecord]:
    """Loads users from users.json with mtime-based caching."""
    global _cached_users_mtime_ns, _cached_users_path, _cached_users_by_name

    with _users_file_lock:
        ensure_users_file()
        stat = USERS_FILE.stat()
        if (
            not force_reload
            and _cached_users_path == USERS_FILE
            and _cached_users_mtime_ns == stat.st_mtime_ns
            and _cached_users_by_name
        ):
            return _cached_users_by_name

        raw_data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        raw_users = raw_data.get("users", []) if isinstance(raw_data, dict) else raw_data
        registry: Dict[str, UserRecord] = {}
        if isinstance(raw_users, list):
            for entry in raw_users:
                if not isinstance(entry, dict):
                    continue
                rec = _parse_user_dict(entry)
                if rec is not None:
                    registry[rec.username] = rec

        _cached_users_mtime_ns = stat.st_mtime_ns
        _cached_users_path = USERS_FILE
        _cached_users_by_name = registry
        return registry


def list_users_public() -> List[Dict[str, Any]]:
    """Returns non-sensitive metadata for all users in users.json."""
    registry = get_user_registry()
    return [
        {
            "username": rec.username,
            "email": rec.email,
            "role": rec.role,
            "floor": rec.floor,
            "default_floor": rec.default_floor,
            "allowed_floors": list(rec.allowed_floors),
            "active": rec.active,
        }
        for rec in registry.values()
    ]


def get_user_by_username(username: str) -> Optional[UserRecord]:
    return get_user_registry().get(username.strip())


def get_user_by_email(email: str) -> Optional[UserRecord]:
    norm = email.strip().lower()
    if not norm:
        return None
    for rec in get_user_registry().values():
        if rec.email.strip().lower() == norm:
            return rec
    return None


def update_user_password(username: str, new_password: str) -> UserRecord:
    """Hashes new_password with PBKDF2-HMAC-SHA256 and persists to users.json atomically."""
    if len(new_password) < MIN_PASSWORD_LENGTH or len(new_password) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"Password must be between {MIN_PASSWORD_LENGTH} and {MAX_PASSWORD_LENGTH} characters.")

    with _users_file_lock:
        registry = get_user_registry(force_reload=True)
        existing = registry.get(username.strip())
        if existing is None:
            raise KeyError(f"User '{username}' not found.")

        new_hash = hash_password_pbkdf2(new_password)
        updated = UserRecord(
            username=existing.username,
            email=existing.email,
            role=existing.role,
            floor=existing.floor,
            default_floor=existing.default_floor,
            allowed_floors=existing.allowed_floors,
            password_hash=new_hash,
            active=True,
        )
        registry[updated.username] = updated
        _write_users_payload_atomic([_record_to_dict(u) for u in registry.values()], USERS_FILE)
        get_user_registry(force_reload=True)
        logger.info(f"Updated PBKDF2 password hash for user '{sanitize_for_log(updated.username, 64)}' in users.json.")
        return updated


def update_user_email(username: str, new_email: str) -> UserRecord:
    """Updates a user's email address in users.json."""
    cleaned_email = new_email.strip()
    if not re.fullmatch(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", cleaned_email):
        raise ValueError("فرمت ایمیل معتبر نیست.")

    with _users_file_lock:
        registry = get_user_registry(force_reload=True)
        existing = registry.get(username.strip())
        if existing is None:
            raise KeyError(f"User '{username}' not found.")

        for other in registry.values():
            if other.username != existing.username and other.email.strip().lower() == cleaned_email.lower():
                raise ValueError("این ایمیل قبلاً برای کاربر دیگری ثبت شده است.")

        updated = UserRecord(
            username=existing.username,
            email=cleaned_email,
            role=existing.role,
            floor=existing.floor,
            default_floor=existing.default_floor,
            allowed_floors=existing.allowed_floors,
            password_hash=existing.password_hash,
            active=existing.active,
        )
        registry[updated.username] = updated
        _write_users_payload_atomic([_record_to_dict(u) for u in registry.values()], USERS_FILE)
        get_user_registry(force_reload=True)
        logger.info(f"Updated email for user '{sanitize_for_log(updated.username, 64)}' in users.json.")
        return updated


def validate_startup_auth_config() -> None:
    """Validates critical auth settings & initializes users.json on startup."""
    get_jwt_secret_bytes()
    registry = get_user_registry()
    if is_production_env():
        missing = [uname for uname, rec in registry.items() if not rec.active]
        if missing:
            logger.error(
                f"Production mode active with disabled/unconfigured users: {', '.join(missing)} (fail-closed)."
            )
    else:
        if not (os.getenv("AUTH_JWT_SECRET", "").strip() or os.getenv("JWT_SECRET", "").strip()):
            logger.warning(
                "AUTH_JWT_SECRET is not set; using an ephemeral per-process HS256 secret (non-production mode)."
            )


# ---------------------------------------------------------------------------
# Forgot Password (Email OTP via SMTP with Terminal Fallback)
# ---------------------------------------------------------------------------
def _hash_otp(email_norm: str, otp: str) -> str:
    return hmac.new(get_jwt_secret_bytes(), f"{email_norm}:{otp}".encode("utf-8"), hashlib.sha256).hexdigest()


def _send_otp_via_smtp(recipient_email: str, username: str, otp: str) -> bool:
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    if not smtp_host:
        return False

    smtp_port = int(os.getenv("SMTP_PORT", "587").strip() or "587")
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "").strip()
    smtp_from = os.getenv("SMTP_FROM", "").strip() or smtp_user or "noreply@building.local"
    use_tls = os.getenv("SMTP_USE_TLS", "true").strip().lower() != "false"
    use_ssl = os.getenv("SMTP_USE_SSL", "false").strip().lower() == "true"

    msg = EmailMessage()
    msg["Subject"] = "کد بازیابی رمز عبور - کنترل صوتی ساختمان هوشمند"
    msg["From"] = smtp_from
    msg["To"] = recipient_email
    msg.set_content(
        f"سلام {username},\n\n"
        f"کد تایید ۶ رقمی شما برای بازیابی رمز عبور: {otp}\n"
        f"این کد به مدت ۵ دقیقه معتبر است.\n\n"
        "در صورتی که این درخواست توسط شما ثبت نشده، این پیام را نادیده بگیرید."
    )

    if use_ssl:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=10) as server:
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
            server.ehlo()
            if use_tls:
                server.starttls()
                server.ehlo()
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.send_message(msg)
    return True


def request_password_reset_otp(email: str) -> Dict[str, Any]:
    """Generates a 6-digit OTP (valid 5 mins), sends via SMTP or prints to terminal fallback."""
    email_clean = email.strip()
    email_norm = email_clean.lower()
    user_rec = get_user_by_email(email_norm)
    if user_rec is None or not user_rec.active:
        raise GatewayAuthError(
            status_code=404,
            detail="حساب کاربری فعالی با این ایمیل یافت نشد.",
            error_code="EMAIL_NOT_FOUND",
            stage="auth",
            headers=NO_STORE_HEADERS,
        )

    otp_code = f"{secrets.randbelow(1000000):06d}"
    otp_hash = _hash_otp(email_norm, otp_code)
    expires_at = time.time() + OTP_TTL_SECONDS

    with _otp_lock:
        # Prune expired entries opportunistically
        now = time.time()
        expired = [k for k, v in _otp_store.items() if v.expires_at <= now]
        for k in expired:
            _otp_store.pop(k, None)
        _otp_store[email_norm] = _OTPEntry(
            username=user_rec.username,
            email=user_rec.email,
            otp_hash=otp_hash,
            expires_at=expires_at,
            attempts=0,
        )

    delivery = "smtp"
    try:
        sent = _send_otp_via_smtp(user_rec.email, user_rec.username, otp_code)
        if not sent:
            raise RuntimeError("SMTP_HOST not configured")
        logger.info(f"Sent password reset OTP via SMTP to '{sanitize_for_log(user_rec.email, 100)}'.")
    except Exception as smtp_exc:
        delivery = "terminal"
        logger.warning(
            f"SMTP delivery unavailable/failed ({smtp_exc}); falling back to terminal OTP output."
        )
        print(
            "\n"
            "============================================================\n"
            f"[PASSWORD RESET OTP - TERMINAL FALLBACK]\n"
            f"User   : {user_rec.username}\n"
            f"Email  : {user_rec.email}\n"
            f"OTP    : {otp_code}\n"
            f"Valid  : 5 minutes\n"
            "============================================================\n",
            flush=True,
        )

    return {
        "ok": True,
        "delivery": delivery,
        "expires_in_seconds": OTP_TTL_SECONDS,
        "message": (
            "کد تایید ۶ رقمی به ایمیل شما ارسال شد."
            if delivery == "smtp"
            else "کد تایید ۶ رقمی ایجاد شد و در کنسول سرور (ترمینال) چاپ گردید."
        ),
    }


def _validate_otp_entry(email: str, otp: str, consume: bool = False) -> _OTPEntry:
    email_norm = email.strip().lower()
    otp_clean = re.sub(r"\s+", "", otp.strip())
    if not re.fullmatch(r"\d{6}", otp_clean):
        raise GatewayAuthError(
            status_code=422,
            detail="کد تایید باید دقیقاً ۶ رقم باشد.",
            error_code="INVALID_OTP_FORMAT",
            stage="auth",
            headers=NO_STORE_HEADERS,
        )

    with _otp_lock:
        entry = _otp_store.get(email_norm)
        now = time.time()
        if entry is None:
            raise GatewayAuthError(
                status_code=400,
                detail="کد تاییدی برای این ایمیل ثبت نشده یا منقضی شده است.",
                error_code="OTP_NOT_FOUND",
                stage="auth",
                headers=NO_STORE_HEADERS,
            )
        if now >= entry.expires_at:
            _otp_store.pop(email_norm, None)
            raise GatewayAuthError(
                status_code=400,
                detail="کد تایید منقضی شده است. لطفاً دوباره درخواست کد دهید.",
                error_code="OTP_EXPIRED",
                stage="auth",
                headers=NO_STORE_HEADERS,
            )
        if entry.attempts >= OTP_MAX_ATTEMPTS:
            _otp_store.pop(email_norm, None)
            raise GatewayAuthError(
                status_code=429,
                detail="تعداد تلاش‌های اشتباه بیش از حد مجاز بود. لطفاً کد جدید دریافت کنید.",
                error_code="OTP_MAX_ATTEMPTS",
                stage="auth",
                headers=NO_STORE_HEADERS,
            )

        expected_hash = _hash_otp(email_norm, otp_clean)
        if not hmac.compare_digest(expected_hash, entry.otp_hash):
            entry.attempts += 1
            remaining = max(0, OTP_MAX_ATTEMPTS - entry.attempts)
            if remaining == 0:
                _otp_store.pop(email_norm, None)
            raise GatewayAuthError(
                status_code=401,
                detail="کد تایید ۶ رقمی نادرست است.",
                error_code="INVALID_OTP",
                stage="auth",
                headers=NO_STORE_HEADERS,
            )

        if consume:
            _otp_store.pop(email_norm, None)
        return entry


def verify_password_reset_otp(email: str, otp: str) -> Dict[str, Any]:
    """Verifies a 6-digit OTP without consuming it so user can proceed to set a new password."""
    entry = _validate_otp_entry(email, otp, consume=False)
    return {
        "ok": True,
        "verified": True,
        "username": entry.username,
        "message": "کد تایید با موفقیت احراز شد. اکنون رمز عبور جدید را وارد کنید.",
    }


def reset_password_with_otp(email: str, otp: str, new_password: str) -> Dict[str, Any]:
    """Consumes the 6-digit OTP and writes the new PBKDF2 password hash into users.json."""
    if len(new_password) < MIN_PASSWORD_LENGTH or len(new_password) > MAX_PASSWORD_LENGTH:
        raise GatewayAuthError(
            status_code=422,
            detail=f"رمز عبور جدید باید حداقل {MIN_PASSWORD_LENGTH} کاراکتر باشد.",
            error_code="WEAK_PASSWORD",
            stage="auth",
            headers=NO_STORE_HEADERS,
        )

    entry = _validate_otp_entry(email, otp, consume=True)
    updated = update_user_password(entry.username, new_password)
    return {
        "ok": True,
        "username": updated.username,
        "message": "رمز عبور با موفقیت تغییر یافت. اکنون می‌توانید وارد شوید.",
    }


# ---------------------------------------------------------------------------
# Rate Limiting
# ---------------------------------------------------------------------------
def check_login_rate_limit(client_ip: str) -> Optional[int]:
    max_attempts = _get_login_max_attempts()
    if max_attempts <= 0:
        return None
    window_seconds = _get_login_window_seconds()
    now = time.monotonic()
    with _login_rate_lock:
        dq = _login_failures_by_ip.get(client_ip)
        if dq is None:
            return None
        while dq and now - dq[0] >= window_seconds:
            dq.popleft()
        if not dq:
            _login_failures_by_ip.pop(client_ip, None)
            return None
        _login_failures_by_ip.move_to_end(client_ip)
        if len(dq) >= max_attempts:
            return max(1, int(math.ceil(window_seconds - (now - dq[0]))))
    return None


def clear_login_failures(client_ip: str) -> None:
    with _login_rate_lock:
        _login_failures_by_ip.pop(client_ip, None)


def _prune_rate_limit_entries(now: float, window_seconds: int) -> None:
    expired_ips: List[str] = []
    for ip, dq in _login_failures_by_ip.items():
        while dq and now - dq[0] >= window_seconds:
            dq.popleft()
        if not dq:
            expired_ips.append(ip)
    for ip in expired_ips:
        _login_failures_by_ip.pop(ip, None)


def record_login_failure(client_ip: str) -> None:
    max_attempts = _get_login_max_attempts()
    if max_attempts <= 0:
        return
    window_seconds = _get_login_window_seconds()
    now = time.monotonic()
    with _login_rate_lock:
        dq = _login_failures_by_ip.get(client_ip)
        if dq is None:
            if len(_login_failures_by_ip) >= MAX_RATE_LIMIT_IPS:
                _prune_rate_limit_entries(now, window_seconds)
            while len(_login_failures_by_ip) >= MAX_RATE_LIMIT_IPS:
                _login_failures_by_ip.popitem(last=False)
            dq = deque()
            _login_failures_by_ip[client_ip] = dq
        while dq and now - dq[0] >= window_seconds:
            dq.popleft()
        dq.append(now)
        _login_failures_by_ip.move_to_end(client_ip)


# ---------------------------------------------------------------------------
# HS256 JWT Creation & Verification
# ---------------------------------------------------------------------------
def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    if not segment or not re.fullmatch(r"[A-Za-z0-9_-]+", segment):
        raise ValueError("Invalid base64url segment.")
    padded = segment + ("=" * (-len(segment) % 4))
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _is_finite_numeric(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def create_access_token(user_record: UserRecord, ttl_seconds: Optional[int] = None) -> Tuple[str, int]:
    ttl = get_token_ttl_seconds() if ttl_seconds is None else int(ttl_seconds)
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": JWT_ISSUER,
        "sub": user_record.username,
        "role": user_record.role,
        "floor": user_record.floor,
        "allowed_floors": list(user_record.allowed_floors),
        "iat": now,
        "exp": now + ttl,
        "jti": secrets.token_hex(16),
    }
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = hmac.new(get_jwt_secret_bytes(), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64url_encode(sig)}", ttl


def verify_access_token(token: str) -> AuthUser:
    unauthorized = lambda: GatewayAuthError(
        status_code=401,
        detail="احراز هویت نامعتبر است یا توکن دسترسی ارسال نشده است.",
        error_code="UNAUTHORIZED",
        headers=NO_STORE_HEADERS,
    )

    if not isinstance(token, str) or not token or len(token) > MAX_JWT_LENGTH:
        raise unauthorized()
    if re.search(r"[\s\x00-\x1f\x7f]", token):
        raise unauthorized()
    parts = token.split(".")
    if len(parts) != 3 or any(not p for p in parts):
        raise unauthorized()
    header_b64, payload_b64, sig_b64 = parts

    try:
        header = json.loads(_b64url_decode(header_b64).decode("utf-8"))
        sig_bytes = _b64url_decode(sig_b64)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise unauthorized() from exc

    if (
        not isinstance(header, dict)
        or header.get("alg") != "HS256"
        or header.get("typ") != "JWT"
        or any(k in header for k in ("jku", "x5u", "jwk"))
        or len(sig_bytes) != 32
    ):
        raise unauthorized()

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected_sig = hmac.new(get_jwt_secret_bytes(), signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(sig_bytes, expected_sig):
        raise unauthorized()

    try:
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise unauthorized() from exc

    if not isinstance(payload, dict) or payload.get("iss") != JWT_ISSUER:
        raise unauthorized()

    iat = payload.get("iat")
    exp = payload.get("exp")
    if not _is_finite_numeric(iat) or not _is_finite_numeric(exp):
        raise unauthorized()

    now = int(time.time())
    if iat > now + 30:
        raise unauthorized()
    if now >= exp:
        raise GatewayAuthError(
            status_code=401,
            detail="نشست کاربری منقضی شده است. لطفاً دوباره وارد شوید.",
            error_code="TOKEN_EXPIRED",
            headers=NO_STORE_HEADERS,
        )
    if exp <= iat:
        raise unauthorized()

    jti = payload.get("jti")
    if jti is not None and (not isinstance(jti, str) or not (1 <= len(jti) <= 128)):
        raise unauthorized()

    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        raise unauthorized()

    registry = get_user_registry()
    user_record = registry.get(sub)
    if user_record is None or not user_record.active:
        raise unauthorized()

    token_role = payload.get("role")
    token_floor = payload.get("floor")
    token_allowed = payload.get("allowed_floors")

    if token_role != user_record.role:
        raise unauthorized()

    if user_record.role == "resident":
        if type(token_floor) is not int or token_floor != user_record.floor:
            raise unauthorized()
    else:
        if token_floor != "all":
            raise unauthorized()

    if (
        not isinstance(token_allowed, list)
        or len(token_allowed) != len(user_record.allowed_floors)
        or any(type(f) is not int for f in token_allowed)
        or tuple(sorted(set(token_allowed))) != user_record.allowed_floors
    ):
        raise unauthorized()

    return AuthUser(
        username=user_record.username,
        role=user_record.role,
        floor=user_record.floor,
        default_floor=user_record.default_floor,
        allowed_floors=user_record.allowed_floors,
        auth_method="jwt",
        expires_at=int(exp),
        email=user_record.email,
    )


def authenticate_bearer_header(auth_header: str) -> AuthUser:
    scheme, _, raw_token = auth_header.partition(" ")
    token = raw_token.strip()
    if scheme.lower() != "bearer" or not token:
        raise GatewayAuthError(
            status_code=401,
            detail="احراز هویت نامعتبر است یا توکن دسترسی ارسال نشده است.",
            error_code="UNAUTHORIZED",
            headers=NO_STORE_HEADERS,
        )

    expected_api_token = get_voice_api_token()
    if expected_api_token and hmac.compare_digest(
        token.encode("utf-8"), expected_api_token.encode("utf-8")
    ):
        return AuthUser(
            username="api_token",
            role="admin",
            floor="all",
            default_floor=1,
            allowed_floors=(1, 2),
            auth_method="api_token",
            expires_at=None,
            email="",
        )

    return verify_access_token(token)


def check_bearer_auth(request: Request) -> Optional[JSONResponse]:
    """Fail-closed Bearer authenticator (HS256 JWT or explicit M2M VOICE_API_TOKEN)."""
    auth_header = request.headers.get("Authorization", "")
    try:
        auth_user = authenticate_bearer_header(auth_header)
        request.state.auth_user = auth_user
        return None
    except GatewayAuthError as exc:
        logger.warning(
            f"Unauthorized request ({exc.error_code}) on {request.method} {sanitize_for_log(request.url.path)}"
        )
        return build_error_response(
            status_code=exc.status_code,
            detail=exc.detail,
            stage=exc.stage,
            error_code=exc.error_code,
            headers=exc.headers or NO_STORE_HEADERS,
        )


def require_authenticated_user(request: Request) -> AuthUser:
    """FastAPI dependency guaranteeing fail-closed authenticated user context."""
    auth_user = getattr(request.state, "auth_user", None)
    if isinstance(auth_user, AuthUser):
        return auth_user
    auth_user = authenticate_bearer_header(request.headers.get("Authorization", ""))
    request.state.auth_user = auth_user
    return auth_user


def require_admin_user(request: Request) -> AuthUser:
    """FastAPI dependency enforcing admin role for telemetry and user-management routes."""
    auth_user = require_authenticated_user(request)
    if auth_user.role != "admin":
        raise GatewayAuthError(
            status_code=403,
            detail="دسترسی به پنل مدیریت فقط برای مدیر سیستم (admin) مجاز است.",
            error_code="ADMIN_REQUIRED",
            stage="auth",
            headers=NO_STORE_HEADERS,
        )
    return auth_user


# ---------------------------------------------------------------------------
# Floor Isolation Helpers
# ---------------------------------------------------------------------------
_PERSIAN_EZAFE_SEP = r"(?:[\u200c\u0654\s]+(?:[یي][\u200c\s]+)?|\s*)"
_FLOOR1_EXPLICIT_RE = re.compile(
    rf"(?:طبق[هۀ]{_PERSIAN_EZAFE_SEP}(?:اول|یکم?|۱|1|١)(?!\w)"
    r"|\bfloor\s*(?:#|-)?\s*(?:1|one)\b"
    r"|\b(?:1st|first)\s+floor\b)",
    re.IGNORECASE,
)
_FLOOR2_EXPLICIT_RE = re.compile(
    rf"(?:طبق[هۀ]{_PERSIAN_EZAFE_SEP}(?:دوم|دو|۲|2|٢)(?!\w)"
    r"|\bfloor\s*(?:#|-)?\s*(?:2|two)\b"
    r"|\b(?:2nd|second)\s+floor\b)",
    re.IGNORECASE,
)


def detect_explicit_floors(transcript: str) -> Set[int]:
    """Detects explicit Persian or English references to Floor 1 or Floor 2."""
    if not isinstance(transcript, str) or not transcript.strip():
        return set()
    detected: Set[int] = set()
    if _FLOOR1_EXPLICIT_RE.search(transcript):
        detected.add(1)
    if _FLOOR2_EXPLICIT_RE.search(transcript):
        detected.add(2)
    return detected


def format_floor_forbidden_detail(disallowed_floors: Set[int], allowed_floors: Tuple[int, ...]) -> str:
    digit_map = str.maketrans("12", "۱۲")
    req_str = " و ".join(str(f).translate(digit_map) for f in sorted(disallowed_floors))
    allowed_str = " و ".join(str(f).translate(digit_map) for f in sorted(allowed_floors))
    return f"شما مجاز به کنترل تجهیزات طبقه {req_str} نیستید. دسترسی شما محدود به طبقه {allowed_str} است."


def check_client_floor_override(
    request: Request, auth_user: AuthUser, form_data: Any
) -> Optional[JSONResponse]:
    """Rejects client-supplied floor/target_floor overrides via query params or multipart form fields."""
    override_values: List[Any] = []
    for key in ("floor", "target_floor"):
        override_values.extend(request.query_params.getlist(key))
        if form_data is not None:
            override_values.extend(form_data.getlist(key))

    if not override_values:
        return None

    digit_map = str.maketrans("۱۲١٢", "1212")
    for raw_val in override_values:
        if isinstance(raw_val, str):
            stripped = raw_val.strip().translate(digit_map)
            if stripped in ("1", "2"):
                parsed_floor = int(stripped)
                if parsed_floor not in auth_user.allowed_floors:
                    logger.warning(
                        f"Rejected floor override parameter floor={parsed_floor} for "
                        f"user='{sanitize_for_log(auth_user.username, 64)}' "
                        f"(allowed={auth_user.allowed_floors})"
                    )
                    return build_error_response(
                        status_code=403,
                        detail=format_floor_forbidden_detail({parsed_floor}, auth_user.allowed_floors),
                        stage="auth",
                        error_code="FLOOR_FORBIDDEN",
                        headers=NO_STORE_HEADERS,
                    )

    logger.warning(
        f"Rejected client-supplied floor override on /process-audio for "
        f"user='{sanitize_for_log(auth_user.username, 64)}'"
    )
    return build_error_response(
        status_code=422,
        detail="ارسال پارامتر تغییر طبقه در درخواست مجاز نیست.",
        stage="auth",
        error_code="VALIDATION_ERROR",
        headers=NO_STORE_HEADERS,
    )
