import base64
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from dotenv import load_dotenv
import uvicorn

# Ensure the project root is at the head of sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Ensure CUDA DLLs (e.g. cublas64_12.dll from Ollama or CUDA toolkit) are found on Windows
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

# Load .env variables before reading host and port
load_dotenv(BASE_DIR / ".env")


def is_hotspot_ip_active() -> bool:
    """Checks if 192.168.137.1 is active on any local interface."""
    try:
        host = socket.gethostname()
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            if info[4][0] == "192.168.137.1":
                return True
    except Exception:
        pass
    return False


def ensure_offline_hotspot() -> None:
    """Verifies Loopback Adapter (10.254.254.1), locks registry to 192.168.137.1, and starts Windows Mobile Hotspot."""
    if sys.platform != "win32":
        return

    script_path = BASE_DIR / "scripts" / "start_offline_hotspot.ps1"
    if not script_path.exists():
        return

    try:
        print("[HOTSPOT] Initializing Ava-IoT Offline Hotspot & locking IP 192.168.137.1...", flush=True)
        cmd = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
        for line in result.stdout.splitlines():
            line_str = line.strip()
            if line_str:
                print(line_str)
        if result.returncode != 0 and result.stderr:
            print(f"[HOTSPOT] Notice: {result.stderr.strip()}")

        # Verify whether 192.168.137.1 is bound
        if is_hotspot_ip_active():
            print("[HOTSPOT] [✓] Ava-IoT Offline Hotspot verified active on http://192.168.137.1:8000", flush=True)
        else:
            for _ in range(5):
                time.sleep(1.0)
                if is_hotspot_ip_active():
                    print("[HOTSPOT] [✓] Ava-IoT Offline Hotspot bound to http://192.168.137.1:8000", flush=True)
                    break
    except Exception as exc:
        print(f"[HOTSPOT] Notice during mobile hotspot trigger: {exc}")


class CloudflareTunnelSupervisor:
    """Supervises Cloudflare Tunnel with persistent HTTP/2 protocol and auto-reconnect."""

    def __init__(self, port: int = 8000, target_host: str = "127.0.0.1") -> None:
        self.port = port
        self.target_host = target_host
        self.target_url = f"http://{target_host}:{port}"
        self.token = os.getenv("CLOUDFLARE_TUNNEL_TOKEN", "").strip()
        self.url_file = BASE_DIR / "scripts" / "tunnel_url.txt"
        self.log_file = BASE_DIR / "scripts" / "cloudflared.log"
        self._stop_event = threading.Event()
        self._process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self.current_url: str | None = None

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="CloudflareSupervisor", daemon=True)
        self._thread.start()
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, name="CloudflareHeartbeat", daemon=True)
        self._heartbeat_thread.start()

    def _heartbeat_loop(self) -> None:
        """Sends periodic lightweight health checks every 20s to prevent Cloudflare tunnel idle timeouts."""
        # Initial delay to give tunnel time to start and register
        if self._stop_event.wait(timeout=10.0):
            return

        while not self._stop_event.is_set():
            target = f"{self.current_url}/health" if self.current_url else f"{self.target_url}/health"
            try:
                req = urllib.request.Request(
                    target,
                    headers={"User-Agent": "CloudflareTunnelHeartbeat/1.0"},
                )
                with urllib.request.urlopen(req, timeout=6.0) as resp:
                    pass
            except Exception:
                pass

            if self._stop_event.wait(timeout=20.0):
                break

    def stop(self) -> None:
        self._stop_event.set()
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=3)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass

    def _find_cloudflared(self) -> str:
        candidates = [
            "cloudflared",
            r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
            r"C:\Program Files\cloudflared\cloudflared.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Cloudflare.cloudflared_Microsoft.Winget.Source_8wekyb3d8bbwe\cloudflared.exe"),
        ]
        for candidate in candidates:
            try:
                out = subprocess.run(
                    [candidate, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=4,
                )
                if out.returncode == 0:
                    return candidate
            except Exception:
                continue
        return "cloudflared"

    def _wait_for_backend_health(self, timeout: float = 90.0) -> bool:
        health_url = f"{self.target_url}/health"
        print(f"[TUNNEL] Waiting for backend server to become healthy ({health_url})...", flush=True)
        start = time.time()
        while not self._stop_event.is_set():
            try:
                req = urllib.request.Request(health_url, headers={"User-Agent": "TunnelSupervisor"})
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    if resp.status == 200:
                        print(f"[TUNNEL] Backend server is healthy (HTTP 200). Initializing Cloudflare Tunnel...", flush=True)
                        return True
            except Exception:
                pass
            if time.time() - start > timeout:
                print(f"[TUNNEL] Warning: Backend server health check timed out after {timeout}s.", flush=True)
                return False
            time.sleep(1.0)
        return False

    def _get_local_candidate_ips(self) -> list[str]:
        ips: list[str] = []
        try:
            host = socket.gethostname()
            for info in socket.getaddrinfo(host, None, socket.AF_INET):
                ip = info[4][0]
                if ip.startswith(("127.", "10.254.", "172.23.", "172.24.", "169.254.")):
                    continue
                if ip not in ips:
                    ips.append(ip)
        except Exception:
            pass
        return ips

    def _provision_quick_tunnel(self, source_ip: str | None = None) -> tuple[str | None, str | None]:
        import http.client

        class BoundHandler(urllib.request.HTTPSHandler):
            def https_open(self, req):
                return self.do_open(
                    lambda host, timeout=10: http.client.HTTPSConnection(
                        host, timeout=timeout, source_address=(source_ip, 0) if source_ip else None
                    ),
                    req,
                )

        opener = urllib.request.build_opener(BoundHandler) if source_ip else urllib.request.build_opener()
        req = urllib.request.Request(
            "https://api.trycloudflare.com/tunnel",
            data=b"",
            method="POST",
            headers={"User-Agent": "cloudflared/2026.11.0"},
        )
        try:
            with opener.open(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("success"):
                    res = data["result"]
                    hostname = res.get("hostname")
                    token_dict = {"a": res["account_tag"], "t": res["id"], "s": res["secret"]}
                    token = base64.b64encode(json.dumps(token_dict).encode("utf-8")).decode("utf-8")
                    return hostname, token
        except Exception:
            pass
        return None, None

    def _obtain_resilient_tunnel(self) -> tuple[str | None, str | None]:
        # Try default route first, then physical network interfaces to bypass rate limiting (429)
        candidates = [None] + self._get_local_candidate_ips()
        for ip in candidates:
            if self._stop_event.is_set():
                break
            try:
                hostname, token = self._provision_quick_tunnel(ip)
                if hostname and token:
                    bind_label = f"bind={ip}" if ip else "default route"
                    print(f"[TUNNEL] Quick tunnel successfully provisioned via {bind_label} -> {hostname}", flush=True)
                    return hostname, token
            except Exception:
                continue
        return None, None

    def _print_banner(self, url: str) -> None:
        banner = f"""
======================================================================
  [CLOUDFLARE TUNNEL LIVE] Protocol: HTTP/2
  Public URL : {url}
  Local Target: {self.target_url}
  Status     : Operational & Connected
  Saved To   : {self.url_file}
======================================================================
"""
        print(banner, flush=True)

    def _run_loop(self) -> None:
        cf_bin = self._find_cloudflared()
        self.url_file.parent.mkdir(parents=True, exist_ok=True)

        url_regex = re.compile(r"https://(?!api\.)[a-zA-Z0-9-]+\.trycloudflare\.com")

        # 1. Wait for backend server to become healthy
        if not self._wait_for_backend_health(timeout=90.0):
            if self._stop_event.is_set():
                return
            print("[TUNNEL] Backend did not report healthy. Tunnel startup aborted.", flush=True)
            return

        while not self._stop_event.is_set():
            token = os.getenv("CLOUDFLARE_TUNNEL_TOKEN", "").strip()
            hostname = None

            if token and len(token) > 10:
                cmd = [
                    cf_bin,
                    "tunnel",
                    "--no-autoupdate",
                    "--edge-ip-version",
                    "4",
                    "--protocol",
                    "http2",
                    "run",
                    "--token",
                    token,
                    "--url",
                    self.target_url,
                ]
            else:
                # 2. Resilient quick tunnel provisioning to bypass VPN/proxy 429 rate limits
                hostname, res_token = self._obtain_resilient_tunnel()
                if hostname and res_token:
                    self.current_url = f"https://{hostname}"
                    try:
                        self.url_file.write_text(self.current_url, encoding="utf-8")
                    except Exception:
                        pass
                    self._print_banner(self.current_url)
                    cmd = [
                        cf_bin,
                        "tunnel",
                        "--no-autoupdate",
                        "--edge-ip-version",
                        "4",
                        "run",
                        "--token",
                        res_token,
                        "--url",
                        self.target_url,
                        "--protocol",
                        "http2",
                    ]
                else:
                    cmd = [
                        cf_bin,
                        "tunnel",
                        "--no-autoupdate",
                        "--edge-ip-version",
                        "4",
                        "--protocol",
                        "http2",
                        "--url",
                        self.target_url,
                    ]

            try:
                print(f"[TUNNEL] Launching Cloudflare Tunnel process ({cf_bin}) -> {self.target_url}...", flush=True)
                self._process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )

                found_url = bool(hostname or token)
                recent_lines: list[str] = []
                assert self._process.stdout is not None
                for raw_line in iter(self._process.stdout.readline, ""):
                    if self._stop_event.is_set():
                        break
                    line = raw_line.strip()
                    if not line:
                        continue
                    recent_lines.append(line)
                    if len(recent_lines) > 25:
                        recent_lines.pop(0)

                    match = url_regex.search(line)
                    if match and not found_url:
                        self.current_url = match.group(0)
                        found_url = True
                        try:
                            self.url_file.write_text(self.current_url, encoding="utf-8")
                        except Exception:
                            pass
                        self._print_banner(self.current_url)

                    # Print critical registration, connection or error lines
                    line_lower = line.lower()
                    if any(k in line_lower for k in ("registered tunnel connection", "registered connection", "error", "failed", "status 429", "warning", "fatal")):
                        print(f"[TUNNEL:cloudflared] {line}", flush=True)

                self._process.wait()
                exit_code = self._process.returncode
                if self._stop_event.is_set():
                    break

                print(f"[TUNNEL] Warning: cloudflared process terminated with exit code {exit_code}.", flush=True)
                if exit_code != 0 and recent_lines:
                    print("[TUNNEL] Details from cloudflared output / stderr:", flush=True)
                    for diag in recent_lines[-8:]:
                        print(f"  | {diag}", flush=True)

                print("[TUNNEL] Re-establishing tunnel in 5s...", flush=True)
                time.sleep(5)
            except Exception as exc:
                if self._stop_event.is_set():
                    break
                print(f"[TUNNEL] Error during tunnel execution: {exc}. Retrying in 5s...", flush=True)
                time.sleep(5)


def create_dual_stack_socket(host: str, port: int) -> socket.socket:
    """Creates a dual-stack socket that accepts both IPv4 and IPv6 connections on Windows."""
    if host in ("::", "::0", ""):
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except OSError:
            pass
        sock.bind((host or "::", port))
        sock.set_inheritable(True)
        return sock

    config = uvicorn.Config("app.main:app", host=host, port=port)
    return config.bind_socket()


def main() -> None:
    # 1. Ensure Offline Loopback & Start Windows Mobile Hotspot
    ensure_offline_hotspot()

    # 2. Host & Port Configuration
    host = os.getenv("HOST", "0.0.0.0").strip() or "0.0.0.0"
    port = int(os.getenv("PORT", "8000"))

    # 3. Start Cloudflare Tunnel Supervisor with HTTP/2 protocol
    tunnel_supervisor = CloudflareTunnelSupervisor(port=port, target_host="127.0.0.1")
    tunnel_supervisor.start()

    # 4. Run Uvicorn Server
    reload_env = os.getenv("UVICORN_RELOAD", "").strip().lower()
    reload = reload_env == "true" if reload_env else False

    try:
        if reload:
            uvicorn.run("app.main:app", host=host, port=port, reload=True)
        else:
            server_socket = create_dual_stack_socket(host, port)
            config = uvicorn.Config("app.main:app")
            server = uvicorn.Server(config)
            server.run(sockets=[server_socket])
    finally:
        tunnel_supervisor.stop()


if __name__ == "__main__":
    main()
