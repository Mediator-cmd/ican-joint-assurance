"""Windows-only, secret-free entrypoint for the packaged offline demonstration."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from datetime import UTC, datetime
from pathlib import Path


STATE_SCHEMA_VERSION = 1
OFFLINE_APP_ID = "ican-joint-assurance-offline"
AI_ENVIRONMENT_NAMES = (
    "AI_API_KEY",
    "AI_BASE_URL",
    "AI_MODEL",
    "AI_TIMEOUT_SECONDS",
)


def content_root() -> Path:
    """Locate PyInstaller's immutable bundled assets."""

    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root).resolve()
    return Path(__file__).resolve().parents[1]


def install_root() -> Path:
    """Locate the writable directory containing the downloaded package."""

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parents[2]
    return Path(__file__).resolve().parents[1]


def state_file(root: Path) -> Path:
    return root / ".runtime" / "offline-state.json"


def port_is_available(port: int) -> bool:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        listener.close()


def find_available_port() -> int:
    for port in range(8000, 8021):
        if port_is_available(port):
            return port
    raise RuntimeError("8000-8020 均已被占用，请关闭占用端口的程序后重试。")


def process_is_running(process_id: int) -> bool:
    if process_id <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information,
            False,
            process_id,
        )
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return ctypes.get_last_error() == 5
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_running_url(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        process_id = int(state["pid"])
        url = str(state["url"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
        return None
    if state.get("schema_version") != STATE_SCHEMA_VERSION or state.get("app_id") != OFFLINE_APP_ID:
        return None
    if not url.startswith("http://127.0.0.1:") or not process_is_running(process_id):
        return None
    return url


def write_state(path: Path, url: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": STATE_SCHEMA_VERSION,
        "app_id": OFFLINE_APP_ID,
        "pid": os.getpid(),
        "executable_path": str(Path(sys.executable).resolve()),
        "url": url,
        "started_at_utc": datetime.now(UTC).isoformat(),
    }
    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)


def remove_own_state(path: Path) -> None:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if int(state.get("pid", 0)) == os.getpid():
            path.unlink(missing_ok=True)
    except (OSError, ValueError, json.JSONDecodeError):
        pass


def open_in_edge(url: str) -> None:
    edge_paths = (
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
    )
    for edge_path in edge_paths:
        if edge_path.is_file():
            subprocess.Popen([str(edge_path), "--new-window", url], close_fds=True)
            return
    webbrowser.open(url, new=1)


def open_when_ready(url: str) -> None:
    for _ in range(80):
        try:
            with urllib.request.urlopen(f"{url}/api/v1/health", timeout=1) as response:
                if 200 <= response.status < 400:
                    open_in_edge(url)
                    return
        except OSError:
            pass
        time.sleep(0.25)


def configure_offline_environment(content: Path, writable_root: Path, port: int) -> None:
    for name in AI_ENVIRONMENT_NAMES:
        os.environ.pop(name, None)
    os.environ["APP_CONTENT_ROOT"] = str(content)
    os.environ["APP_FRONTEND_DIST_PATH"] = str(content / "frontend" / "dist")
    os.environ["APP_RUNTIME_DATABASE_PATH"] = str(
        writable_root / ".runtime" / "runtime-sessions.sqlite3"
    )
    os.environ["APP_MAX_REQUEST_BODY_BYTES"] = "2097152"
    os.environ["PORT"] = str(port)


def main() -> None:
    writable_root = install_root()
    state_path = state_file(writable_root)
    existing_url = read_running_url(state_path)
    if existing_url:
        print(f"[INFO] 离线演示已在运行：{existing_url}")
        open_in_edge(existing_url)
        return

    port = find_available_port()
    url = f"http://127.0.0.1:{port}"
    configure_offline_environment(content_root(), writable_root, port)
    write_state(state_path, url)
    print("[INFO] 联保智调离线演示正在启动……")
    print(f"[INFO] 地址：{url}")
    print("[INFO] 当前为离线确定性 AI 辅助模式，未加载任何 API 密钥。")
    threading.Thread(target=open_when_ready, args=(url,), daemon=True).start()

    try:
        import uvicorn
        from backend.app.main import app

        uvicorn.run(
            app,
            host="127.0.0.1",
            port=port,
            workers=1,
            server_header=False,
            timeout_keep_alive=5,
        )
    finally:
        remove_own_state(state_path)


if __name__ == "__main__":
    main()
