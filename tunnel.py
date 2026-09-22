"""Cloudflare Quick Tunnel + local HTTP file server.

Exposes bytes written into ./upload_cache/ as public HTTPS URLs of the form
    https://{random}.trycloudflare.com/{sha256-prefix}{ext}?t={session_token}

The session_token is a fresh random string rotated on every app start, so
previously-logged URLs die when the app restarts.
"""

from __future__ import annotations

import atexit
import hashlib
import platform
import queue
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import streamlit as st


class TunnelError(RuntimeError):
    pass


_ROOT = Path(__file__).parent
UPLOAD_DIR = _ROOT / "upload_cache"
BIN_DIR = _ROOT / "bin"
LOG_DIR = _ROOT / "logs"
_STARTUP_TIMEOUT_SEC = 30
_CF_URL_RE = re.compile(r"https://[a-zA-Z0-9\-]+\.trycloudflare\.com")


def _pick_binary() -> Path:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin":
        arch = "arm64" if machine in ("arm64", "aarch64") else "amd64"
        return BIN_DIR / f"cloudflared-darwin-{arch}"
    if system == "linux":
        arch = "arm64" if machine in ("aarch64", "arm64") else "amd64"
        return BIN_DIR / f"cloudflared-linux-{arch}"
    if system == "windows":
        bundled = BIN_DIR / "cloudflared-windows-amd64.exe"
        if bundled.exists():
            return bundled
        # Path("cloudflared").exists() 只查工作目录不查 PATH，winget 安装（如
        # C:\Program Files (x86)\cloudflared）会误判为未安装——用 shutil.which 才是真 PATH 查找
        found = shutil.which("cloudflared")
        if found:
            return Path(found)
        return Path("cloudflared")
    raise TunnelError(f"unsupported platform: {system}/{machine}")


class _TokenHandler(BaseHTTPRequestHandler):
    tunnel: "Tunnel | None" = None

    def log_message(self, *args, **kwargs):
        return

    def _serve_get(self, write_body: bool) -> None:
        if self.tunnel is None:
            self.send_error(503, "tunnel not ready"); return

        parsed = urllib.parse.urlparse(self.path)
        token = urllib.parse.parse_qs(parsed.query).get("t", [None])[0]
        if not token or token != self.tunnel.session_token:
            self.send_error(403, "forbidden"); return

        name = parsed.path.lstrip("/")
        if not name or "/" in name or ".." in name or name.startswith("."):
            self.send_error(404, "not found"); return

        path = UPLOAD_DIR / name
        try:
            resolved = path.resolve()
            resolved.relative_to(UPLOAD_DIR.resolve())
        except (OSError, ValueError):
            self.send_error(404, "not found"); return
        if not resolved.is_file():
            self.send_error(404, "not found"); return

        data = resolved.read_bytes()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if write_body:
            self.wfile.write(data)

    def do_GET(self):
        self._serve_get(write_body=True)

    def do_HEAD(self):
        self._serve_get(write_body=False)


class Tunnel:
    def __init__(self) -> None:
        self.public_url: str | None = None
        self.session_token: str = secrets.token_urlsafe(24)
        self.serve_dir: Path = UPLOAD_DIR
        self._server: ThreadingHTTPServer | None = None
        self._server_thread: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None
        self._log_fh = None
        self._port: int | None = None

    def start(self) -> None:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        LOG_DIR.mkdir(parents=True, exist_ok=True)

        _TokenHandler.tunnel = self
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _TokenHandler)
        self._port = self._server.server_address[1]
        self._server_thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._server_thread.start()

        binary = _pick_binary()
        if not binary.is_absolute() and not binary.exists():
            raise TunnelError(
                "cloudflared not found on PATH. "
                "Install via `winget install cloudflare.cloudflared` "
                "or drop the binary into ./bin/"
            )
        if binary.is_absolute() and not binary.exists():
            self._shutdown_server()
            raise TunnelError(
                f"cloudflared binary missing: {binary}. "
                "Re-clone the repo or download from "
                "https://github.com/cloudflare/cloudflared/releases"
            )
        try:
            if binary.is_file():
                binary.chmod(0o755)
        except OSError:
            pass

        log_path = LOG_DIR / "cloudflared.log"
        self._log_fh = open(log_path, "ab", buffering=0)

        self._proc = subprocess.Popen(
            [
                str(binary),
                "tunnel",
                "--url", f"http://127.0.0.1:{self._port}",
                "--no-autoupdate",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        url_q: queue.Queue[str | None] = queue.Queue()

        def _reader() -> None:
            found = False
            stdout = self._proc.stdout if self._proc else None
            if stdout is None:
                url_q.put(None); return
            for raw in iter(stdout.readline, b""):
                try:
                    self._log_fh.write(raw)
                except Exception:
                    pass
                if not found:
                    m = _CF_URL_RE.search(raw.decode("utf-8", "replace"))
                    if m:
                        found = True
                        url_q.put(m.group(0))
            if not found:
                url_q.put(None)

        threading.Thread(target=_reader, daemon=True).start()

        try:
            url = url_q.get(timeout=_STARTUP_TIMEOUT_SEC)
        except queue.Empty:
            url = None

        if not url:
            self.shutdown()
            raise TunnelError(
                f"cloudflared startup timeout ({_STARTUP_TIMEOUT_SEC}s). "
                f"See {log_path}"
            )

        self.public_url = url
        atexit.register(self.shutdown)

    def publish_bytes(self, data: bytes, ext: str) -> str:
        if not self.public_url:
            raise TunnelError("tunnel not started")
        ext = ext.lower()
        if not ext.startswith("."):
            ext = "." + ext if ext else ".bin"
        sha = hashlib.sha256(data).hexdigest()[:32]
        fname = f"{sha}{ext}"
        path = UPLOAD_DIR / fname
        if not path.exists():
            path.write_bytes(data)
        return f"{self.public_url}/{fname}?t={self.session_token}"

    def is_alive(self) -> bool:
        return (
            self._proc is not None
            and self._proc.poll() is None
            and self._server is not None
        )

    def _shutdown_server(self) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None

    def shutdown(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            except Exception:
                pass
            self._proc = None
        self._shutdown_server()
        if self._log_fh is not None:
            try:
                self._log_fh.close()
            except Exception:
                pass
            self._log_fh = None


@st.cache_resource
def get_tunnel() -> Tunnel:
    t = Tunnel()
    t.start()
    return t
