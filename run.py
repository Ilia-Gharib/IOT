import os
import sys
from pathlib import Path

from dotenv import load_dotenv
import uvicorn

# Ensure the project root is at the head of sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Load .env variables before reading host and port
load_dotenv(BASE_DIR / ".env")


def main() -> None:
    host = os.getenv("HOST", "0.0.0.0").strip() or "0.0.0.0"
    port = int(os.getenv("PORT", "8000"))
    reload = os.getenv("APP_ENV", "development").strip().lower() == "development"
    uvicorn.run("app.main:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    main()
