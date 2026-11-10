import os
import json
import uuid
import shutil
import logging
import tempfile
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any

import requests
import edge_tts
import ollama
from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
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
TTS_VOICE = os.getenv("TTS_VOICE", "fa-IR-DilaraNeural")      # یا "fa-IR-FaridNeural"

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
TEMP_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
LATEST_AUDIO_PATH = TEMP_AUDIO_DIR / "latest_response.mp3"

whisper_model: Optional[WhisperModel] = None


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------
class LLMCommandSchema(BaseModel):
    floor: int = Field(description="Target floor number: 1 or 2")
    domain: str = Field(description="Home Assistant domain: light, switch, climate, cover, fan")
    service: str = Field(description="Home Assistant service: turn_on, turn_off, set_temperature, toggle")
    entity_id: str = Field(description="Full Home Assistant entity_id, e.g., light.living_room")
    service_data: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Extra parameters like temperature")
    ack_message: str = Field(description="Immediate natural Persian confirmation message")
    success_message: str = Field(description="Natural Persian post-execution spoken response")


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
        whisper_model = WhisperModel(WHISPER_MODEL_SIZE, device="auto", compute_type="auto")
        logger.info("Faster-Whisper loaded successfully.")
    except Exception as e:
        logger.warning(f"Auto device initialization failed ({e}). Falling back to CPU (int8)...")
        whisper_model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
        logger.info("Faster-Whisper loaded on CPU.")

    yield

    # پاکسازی فایل‌های صوتی موقت هنگام خاموش شدن سرور
    shutil.rmtree(TEMP_AUDIO_DIR, ignore_errors=True)
    logger.info("Cleaned up temporary audio directory.")


app = FastAPI(
    title="Smart Building Local Voice Control Gateway",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------
def transcribe_persian_audio(audio_path: Path) -> str:
    """Transcribes audio file to Persian text using Faster-Whisper."""
    if whisper_model is None:
        raise RuntimeError("Whisper model is not initialized.")

    segments, info = whisper_model.transcribe(
        str(audio_path),
        language="fa",
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=400),
    )
    transcript = " ".join(segment.text.strip() for segment in segments).strip()
    logger.info(f"Transcribed Speech ({info.language_probability:.2f}): {transcript}")
    return transcript


def reason_with_qwen(transcript: str) -> LLMCommandSchema:
    """Sends transcribed Persian text to local Ollama (Qwen) and parses strict JSON."""
    logger.info(f"Querying Ollama model '{OLLAMA_MODEL}'...")
    response = ollama.chat(
        model=OLLAMA_MODEL,
        format="json",
        options={"temperature": 0.1},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": transcript},
        ],
    )
    raw_content = response["message"]["content"]
    logger.info(f"Ollama JSON Output: {raw_content}")

    parsed_dict = json.loads(raw_content)
    # اطمینان از معتبر بودن شماره طبقه
    if parsed_dict.get("floor") not in (1, 2):
        parsed_dict["floor"] = 1

    return LLMCommandSchema(**parsed_dict)


def execute_home_assistant_service(cmd: LLMCommandSchema) -> Dict[str, Any]:
    """Dispatches REST API call to the target floor's Home Assistant Docker container."""
    ha_target = HA_INSTANCES.get(cmd.floor, HA_INSTANCES[1])
    base_url = ha_target["url"].rstrip("/")
    token = ha_target["token"]

    endpoint = f"{base_url}/api/services/{cmd.domain}/{cmd.service}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {"entity_id": cmd.entity_id}
    if cmd.service_data:
        payload.update(cmd.service_data)

    logger.info(f"Dispatching HA request -> Floor {cmd.floor}: POST {endpoint} | Payload: {payload}")

    try:
        resp = requests.post(endpoint, headers=headers, json=payload, timeout=5)
        resp.raise_for_status()
        return {"ok": True, "status_code": resp.status_code, "simulated": False}
    except requests.exceptions.RequestException as exc:
        logger.warning(f"Home Assistant connection warning on Floor {cmd.floor}: {exc}")
        # در صورتی که کانتینر HA در محیط تست خاموش باشد، خطا ثبت شده اما چرخه صوتی متوقف نمی‌شود
        return {
            "ok": False,
            "simulated": True,
            "error": str(exc),
        }


async def synthesize_persian_tts(text: str, output_path: Path) -> None:
    """Synthesizes Persian text into an MP3 file using Edge-TTS."""
    logger.info(f"Synthesizing TTS with voice '{TTS_VOICE}': {text}")
    communicate = edge_tts.Communicate(text=text, voice=TTS_VOICE)
    await communicate.save(str(output_path))


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def serve_pwa_client():
    """Serves the mobile web interface directly."""
    index_file = Path(__file__).parent / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h3>index.html not found next to server.py</h3>", status_code=404)
    return HTMLResponse(content=index_file.read_text(encoding="utf-8"))


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
    suffix = Path(audio.filename or "recording.webm").suffix or ".webm"
    temp_input_fd, temp_input_path_str = tempfile.mkstemp(suffix=suffix, dir=TEMP_AUDIO_DIR)
    os.close(temp_input_fd)
    temp_input_path = Path(temp_input_path_str)

    try:
        # ۱. ذخیره فایل صوتی دریافتی
        content = await audio.read()
        if not content:
            raise HTTPException(status_code=400, detail="فایل صوتی ارسالی خالی است.")
        temp_input_path.write_bytes(content)

        # ۲. تبدیل گفتار به متن (STT)
        transcript = transcribe_persian_audio(temp_input_path)
        if not transcript:
            raise HTTPException(status_code=422, detail="صدایی تشخیص داده نشد. لطفاً واضح‌تر صحبت کنید.")

        # ۳. استنتاج مدل زبانی (Ollama Qwen)
        try:
            command_data = reason_with_qwen(transcript)
        except Exception as llm_err:
            logger.error(f"LLM Reasoning Error: {llm_err}")
            raise HTTPException(
                status_code=502,
                detail=f"خطا در ارتباط یا پردازش مدل Ollama ({OLLAMA_MODEL}): {llm_err}",
            )

        # ۴. ارسال فرمان به Home Assistant طبقه مربوطه
        ha_result = execute_home_assistant_service(command_data)

        # ۵. تولید فایل صوتی پاسخ (TTS)
        response_id = uuid.uuid4().hex[:10]
        tts_output_path = TEMP_AUDIO_DIR / f"response_{response_id}.mp3"
        await synthesize_persian_tts(command_data.success_message, tts_output_path)

        # کپی به عنوان آخرین پاسخ برای اندپوینت مستقیم /play-response
        shutil.copyfile(tts_output_path, LATEST_AUDIO_PATH)

        return {
            "status": "success",
            "transcript": transcript,
            "parsed_command": command_data.model_dump(),
            "ha_execution": ha_result,
            "audio_url": f"/play-response?id={response_id}",
        }

    finally:
        # حذف ایمن فایل صوتی ورودی کاربر
        if temp_input_path.exists():
            temp_input_path.unlink(missing_ok=True)


@app.get("/play-response")
async def play_response(id: Optional[str] = Query(default=None)):
    """Streams the synthesized MP3 response back to the mobile client."""
    if id:
        candidate = TEMP_AUDIO_DIR / f"response_{id}.mp3"
        if candidate.exists():
            return FileResponse(candidate, media_type="audio/mpeg", filename="response.mp3")

    if LATEST_AUDIO_PATH.exists():
        return FileResponse(LATEST_AUDIO_PATH, media_type="audio/mpeg", filename="response.mp3")

    raise HTTPException(status_code=404, detail="فایل صوتی پاسخ یافت نشد.")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
