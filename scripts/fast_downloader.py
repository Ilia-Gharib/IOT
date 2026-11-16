import os
import sys
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import threading

TOTAL_SIZE = 1617884929
URL = "https://huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo/resolve/main/model.bin"
CHUNK_SIZE = 16 * 1024 * 1024  # 16 MB per chunk
MAX_WORKERS = 8

target_dir = Path.home() / ".cache" / "huggingface" / "hub" / "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo" / "snapshots" / "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"
target_dir.mkdir(parents=True, exist_ok=True)
target_file = target_dir / "model.bin"

if target_file.exists() and target_file.stat().st_size == TOTAL_SIZE:
    print(f"[CACHE] model.bin already completely exists ({TOTAL_SIZE} bytes).")
    sys.exit(0)

# Pre-allocate sparse/sized file
print(f"[FAST DOWNLOAD] Pre-allocating {TOTAL_SIZE / (1024*1024):.1f} MB file...")
with open(target_file, "wb") as f:
    f.truncate(TOTAL_SIZE)

chunks = []
start = 0
chunk_idx = 0
while start < TOTAL_SIZE:
    end = min(start + CHUNK_SIZE - 1, TOTAL_SIZE - 1)
    chunks.append((chunk_idx, start, end))
    start = end + 1
    chunk_idx += 1

total_chunks = len(chunks)
completed_bytes = 0
file_lock = threading.Lock()
print_lock = threading.Lock()
start_time = time.time()

print(f"[FAST DOWNLOAD] Initialized: {total_chunks} chunks, {MAX_WORKERS} workers.", flush=True)

def download_chunk(item):
    global completed_bytes
    idx, c_start, c_end = item
    retries = 5
    for attempt in range(retries):
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Range": f"bytes={c_start}-{c_end}"
            }
            resp = requests.get(URL, headers=headers, timeout=25, stream=False)
            if resp.status_code not in (200, 206):
                raise RuntimeError(f"HTTP status {resp.status_code}")

            data = resp.content
            expected = c_end - c_start + 1
            if len(data) != expected:
                raise RuntimeError(f"Length mismatch: got {len(data)}, expected {expected}")

            with file_lock:
                with open(target_file, "r+b") as out_f:
                    out_f.seek(c_start)
                    out_f.write(data)

            with print_lock:
                completed_bytes += len(data)
                elapsed = max(time.time() - start_time, 0.1)
                speed = completed_bytes / elapsed / (1024 * 1024)
                pct = (completed_bytes / TOTAL_SIZE) * 100
                print(f"[PROGRESS] {completed_bytes / (1024*1024):.1f}/{TOTAL_SIZE / (1024*1024):.1f} MB ({pct:.1f}%) | {speed:.2f} MB/s | Chunk {idx+1}/{total_chunks}", flush=True)
            return True
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(1 + attempt)
            else:
                with print_lock:
                    print(f"[ERROR] Chunk {idx+1} failed after {retries} retries: {e}", flush=True)
                return False

with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
    futures = [executor.submit(download_chunk, c) for c in chunks]
    all_success = True
    for f in as_completed(futures):
        if not f.result():
            all_success = False

if all_success and target_file.stat().st_size == TOTAL_SIZE:
    print(f"\n[SUCCESS] Model large-v3-turbo completely downloaded to {target_file}", flush=True)
else:
    print(f"\n[WARNING] Download completed with errors.", flush=True)
    sys.exit(1)
