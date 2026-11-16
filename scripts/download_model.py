import os
import sys
import time
import urllib.request
from pathlib import Path

target_dir = Path.home() / ".cache" / "huggingface" / "hub" / "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo" / "snapshots" / "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"
target_dir.mkdir(parents=True, exist_ok=True)
target_file = target_dir / "model.bin"
temp_file = target_dir / "model.bin.tmp"

url = "https://huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo/resolve/main/model.bin"

if target_file.exists() and target_file.stat().st_size == 1617884929:
    print(f"[CACHE] model.bin already fully exists: {target_file} ({target_file.stat().st_size} bytes)")
    sys.exit(0)

existing_size = temp_file.stat().st_size if temp_file.exists() else 0

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}
if existing_size > 0:
    headers["Range"] = f"bytes={existing_size}-"
    print(f"[DOWNLOAD] Resuming model.bin from {existing_size / (1024*1024):.1f} MB...")
else:
    print(f"[DOWNLOAD] Starting fresh download of model.bin (1.61 GB)...")

req = urllib.request.Request(url, headers=headers)
start_time = time.time()

try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        mode = "ab" if existing_size > 0 and resp.status == 206 else "wb"
        if mode == "wb":
            existing_size = 0

        total_size = int(resp.headers.get("Content-Length", 0)) + existing_size
        downloaded = existing_size

        with open(temp_file, mode) as f:
            chunk_size = 1024 * 1024  # 1 MB chunks
            last_print = time.time()
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)

                if time.time() - last_print > 3:
                    pct = (downloaded / total_size * 100) if total_size > 0 else 0
                    speed = (downloaded - existing_size) / (time.time() - start_time) / (1024 * 1024)
                    print(f"[DOWNLOAD] Progress: {downloaded / (1024*1024):.1f} / {total_size / (1024*1024):.1f} MB ({pct:.1f}%) - Speed: {speed:.2f} MB/s", flush=True)
                    last_print = time.time()

    temp_file.rename(target_file)
    print(f"[SUCCESS] model.bin successfully downloaded to {target_file} ({target_file.stat().st_size} bytes)")
except Exception as e:
    print(f"[ERROR] Download failed: {e}", flush=True)
    sys.exit(1)
