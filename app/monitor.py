import logging
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional

try:
    from app.ha_client import check_ha_floor_health
except ImportError:
    try:
        from ha_client import check_ha_floor_health
    except ImportError:
        from .ha_client import check_ha_floor_health

try:
    import psutil
except ImportError:
    psutil = None  # type: ignore[assignment]

logger = logging.getLogger("SmartBuildingVoiceGateway.Monitor")


def get_hardware_telemetry() -> Dict[str, Any]:
    """Collects real-time CPU %, RAM %, and NVIDIA GPU utilization via psutil and nvidia-smi."""
    cpu_percent: Optional[float] = None
    cpu_count: Optional[int] = None
    ram_percent: Optional[float] = None
    ram_used_gb: Optional[float] = None
    ram_total_gb: Optional[float] = None

    if psutil is not None:
        try:
            cpu_percent = round(float(psutil.cpu_percent(interval=0.1)), 1)
            cpu_count = psutil.cpu_count(logical=True)
            mem = psutil.virtual_memory()
            ram_percent = round(float(mem.percent), 1)
            ram_used_gb = round(mem.used / (1024 ** 3), 2)
            ram_total_gb = round(mem.total / (1024 ** 3), 2)
        except Exception as exc:
            logger.warning(f"psutil telemetry error: {exc}")

    gpu_info: Dict[str, Any] = {
        "available": False,
        "name": None,
        "util_percent": None,
        "memory_used_mb": None,
        "memory_total_mb": None,
        "memory_percent": None,
        "temperature_c": None,
    }
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total,name,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2.5,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            first_line = proc.stdout.strip().splitlines()[0]
            parts = [p.strip() for p in first_line.split(",")]
            if len(parts) >= 4:
                util = float(parts[0])
                mem_used = float(parts[1])
                mem_total = float(parts[2])
                name = parts[3]
                temp = float(parts[4]) if len(parts) >= 5 and parts[4].replace(".", "", 1).isdigit() else None
                mem_pct = round((mem_used / mem_total) * 100.0, 1) if mem_total > 0 else None
                gpu_info = {
                    "available": True,
                    "name": name,
                    "util_percent": round(util, 1),
                    "memory_used_mb": round(mem_used, 0),
                    "memory_total_mb": round(mem_total, 0),
                    "memory_percent": mem_pct,
                    "temperature_c": temp,
                }
    except Exception as exc:
        logger.debug(f"nvidia-smi probe unavailable: {exc}")

    return {
        "psutil_available": psutil is not None,
        "cpu": {
            "percent": cpu_percent,
            "cores": cpu_count,
        },
        "ram": {
            "percent": ram_percent,
            "used_gb": ram_used_gb,
            "total_gb": ram_total_gb,
        },
        "gpu": gpu_info,
    }


def get_network_topology(
    *,
    whisper_ready: bool,
    whisper_model_size: str,
    ollama_model: str,
    ollama_probe_fn: Callable[[], List[str]],
) -> List[Dict[str, Any]]:
    """Checks real-time status of Voice Gateway <-> Whisper <-> Ollama <-> Docker Floor 1 & 2."""
    # 1. Voice Gateway (self)
    nodes: List[Dict[str, Any]] = [
        {
            "id": "voice_gateway",
            "label": "Voice Gateway",
            "subtitle": "FastAPI Core",
            "online": True,
            "detail": "فعال و در حال پاسخگویی",
        },
        {
            "id": "whisper",
            "label": "Whisper",
            "subtitle": f"faster-whisper ({whisper_model_size})",
            "online": bool(whisper_ready),
            "detail": "مدل بارگذاری شده و آماده" if whisper_ready else "مدل بارگذاری نشده",
        },
    ]

    # 3. Ollama (qwen3.5:4b)
    ollama_online = False
    ollama_detail = "سرویس Ollama در دسترس نیست"
    try:
        available_models = ollama_probe_fn()
        base_target = ollama_model.split(":", 1)[0].lower()
        has_exact = ollama_model in available_models or f"{ollama_model}:latest" in available_models
        has_base = any(m.split(":", 1)[0].lower() == base_target for m in available_models)
        if has_exact or has_base:
            ollama_online = True
            ollama_detail = f"آنلاین · مدل {ollama_model} آماده"
        elif available_models:
            ollama_online = True
            ollama_detail = f"سرویس آنلاین (مدل {ollama_model} نصب نیست)"
        else:
            ollama_online = True
            ollama_detail = "سرویس آنلاین (بدون مدل نصب‌شده)"
    except Exception:
        ollama_online = False

    nodes.append(
        {
            "id": "ollama",
            "label": f"Ollama ({ollama_model})",
            "subtitle": "Local LLM",
            "online": ollama_online,
            "detail": ollama_detail,
        }
    )

    # 4 & 5. Docker Floor 1 (:8124) & Floor 2 (:8125)
    for floor in (1, 2):
        ha_status = check_ha_floor_health(floor)
        nodes.append(
            {
                "id": f"ha_floor{floor}",
                "label": f"Docker Floor {floor} (:{ha_status['port']})",
                "subtitle": "Home Assistant",
                "online": bool(ha_status["online"]),
                "auth_ok": bool(ha_status["auth_ok"]),
                "detail": ha_status["detail"],
            }
        )

    return nodes


def collect_admin_system_stats(
    *,
    whisper_ready: bool,
    whisper_model_size: str,
    ollama_model: str,
    ollama_probe_fn: Callable[[], List[str]],
) -> Dict[str, Any]:
    """Aggregates hardware telemetry and live network topology for /api/admin/system-stats."""
    return {
        "timestamp": int(time.time()),
        "hardware": get_hardware_telemetry(),
        "topology": get_network_topology(
            whisper_ready=whisper_ready,
            whisper_model_size=whisper_model_size,
            ollama_model=ollama_model,
            ollama_probe_fn=ollama_probe_fn,
        ),
    }
