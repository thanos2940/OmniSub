"""
OmbiSub System Tray Application & Server Supervisor
===================================================
Runs OmbiSub windowlessly with tray notifications, status polling, and automatic
restart on updates (exit code 42) or unexpected crashes.

Can be run via:
    pythonw.exe backend/tray_app.py [--port 8000] [--data-dir <path>]
    python.exe backend/tray_app.py --headless
"""

import argparse
import ctypes
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# Optional GUI dependencies
try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_PYSTRAY = True
except ImportError:
    HAS_PYSTRAY = False
    pystray = None
    Image = None
    ImageDraw = None

BACKEND_DIR = Path(__file__).resolve().parent
ROOT_DIR = BACKEND_DIR.parent
DEFAULT_PORT = 8000
LOCK_NAME = "ombisub_tray.lock"
_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

logger = logging.getLogger("ombisub.tray")


def _pid_alive(pid: int) -> bool:
    """Check if process with given PID is still running."""
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        pass
    if os.name == "nt":
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_tray_lock(lock_file: Path, port: int) -> bool:
    """Acquire single-instance tray lock, replacing stale locks if owner died."""
    payload = json.dumps({
        "pid": os.getpid(),
        "port": port,
        "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    for _ in range(3):
        try:
            fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            return True
        except FileExistsError:
            try:
                data = json.loads(lock_file.read_text(encoding="utf-8"))
                pid = int(data.get("pid") or 0)
            except Exception:
                pid = 0
            if pid == os.getpid():
                return True
            if pid and _pid_alive(pid):
                logger.warning(f"Another OmbiSub Tray instance (PID {pid}) is already running.")
                return False
            # Stale lock: unlink and retry
            try:
                lock_file.unlink()
            except OSError:
                time.sleep(0.2)
            continue
    return False


def release_tray_lock(lock_file: Path) -> None:
    """Release single-instance tray lock file."""
    try:
        if lock_file.exists():
            data = json.loads(lock_file.read_text(encoding="utf-8"))
            if int(data.get("pid") or 0) == os.getpid():
                lock_file.unlink()
    except Exception:
        pass


class ServerSupervisor:
    """Supervises the backend run_server.py process with auto-respawn and health tracking."""

    def __init__(self, port: int = DEFAULT_PORT, data_dir: Optional[Path] = None,
                 python_exe: Optional[str] = None):
        self.port = port
        self.data_dir = data_dir or (Path(os.environ.get("OMNISUB_DATA_DIR") or BACKEND_DIR).resolve())
        self.python_exe = python_exe or sys.executable
        self.proc: Optional[subprocess.Popen] = None
        self.stopping = False
        self.restarting = False
        self.last_health: Dict[str, Any] = {}
        self.is_healthy = False
        self.lock = threading.Lock()
        self.monitor_thread: Optional[threading.Thread] = None

    def start_server(self) -> None:
        """Launch backend/run_server.py windowlessly with OMBISUB_SUPERVISOR=tray."""
        with self.lock:
            if self.proc is not None and self.proc.poll() is None:
                logger.info(f"Server is already running with PID {self.proc.pid}")
                return

            server_script = BACKEND_DIR / "run_server.py"
            cmd = [
                self.python_exe,
                str(server_script),
                "--port", str(self.port),
                "--data-dir", str(self.data_dir),
            ]
            env = dict(os.environ)
            env["OMBISUB_SUPERVISOR"] = "tray"
            env["OMNISUB_DATA_DIR"] = str(self.data_dir)

            logger.info(f"Starting server: {' '.join(cmd)}")
            self.proc = subprocess.Popen(
                cmd,
                cwd=str(BACKEND_DIR),
                env=env,
                creationflags=_SUBPROCESS_FLAGS,
                close_fds=(sys.platform != "win32"),
            )
            logger.info(f"Server started (PID {self.proc.pid})")

    def stop_server(self, timeout: float = 5.0) -> None:
        """Gracefully stop supervised server process."""
        self.stopping = True
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                return

            logger.info(f"Stopping server PID {self.proc.pid}...")

            # Terminate process if still running
            try:
                if sys.platform == "win32":
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                        capture_output=True,
                        timeout=5,
                        creationflags=_SUBPROCESS_FLAGS,
                    )
                else:
                    self.proc.terminate()
            except Exception:
                pass

            try:
                self.proc.wait(timeout=timeout)
            except (subprocess.TimeoutExpired, Exception):
                try:
                    self.proc.kill()
                except Exception:
                    pass

            logger.info("Server stopped.")

    def restart_server(self) -> None:
        """Restart the supervised server process."""
        logger.info("Restarting server...")
        self.restarting = True
        with self.lock:
            if self.proc is not None and self.proc.poll() is None:
                try:
                    if sys.platform == "win32":
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                            capture_output=True,
                            timeout=5,
                            creationflags=_SUBPROCESS_FLAGS,
                        )
                    else:
                        self.proc.terminate()
                        self.proc.wait(timeout=3.0)
                except Exception:
                    pass
            time.sleep(1.0)
            self.start_server()
        self.restarting = False

    def supervisor_loop(self) -> None:
        """Monitor server process, auto-respawning on exit code 42 (update) or crash."""
        while not self.stopping:
            proc = self.proc
            if proc is not None:
                exit_code = proc.poll()
                if exit_code is not None:
                    logger.warning(f"Server PID {proc.pid} exited with code {exit_code}")
                    if self.stopping:
                        break
                    if not self.restarting:
                        # Exit code 42: update restart requested; any other non-zero/zero: crash respawn
                        logger.info(f"Respawning server in 2s (exit code was {exit_code})...")
                        time.sleep(2.0)
                        if not self.stopping:
                            self.start_server()
            time.sleep(1.0)

    def check_health(self) -> Dict[str, Any]:
        """Poll /api/health to inspect queue and agent runner state."""
        url = f"http://127.0.0.1:{self.port}/api/health"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "OmbiSub-Tray/1.0"})
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    self.last_health = data
                    self.is_healthy = True
                    return data
        except Exception:
            pass
        self.is_healthy = False
        return {}

    def toggle_agent_runner(self) -> bool:
        """Toggle Agent Runner state (start or stop)."""
        health = self.check_health()
        runner_info = health.get("agent_runner") or {}
        is_running = runner_info.get("status") in ("running", "idle") or runner_info.get("enabled")

        action = "stop" if is_running else "start"
        url = f"http://127.0.0.1:{self.port}/api/agent/runner/{action}"
        try:
            req = urllib.request.Request(
                url, data=b"{}", headers={"Content-Type": "application/json"}, method="POST"
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                return resp.status in (200, 204)
        except Exception as e:
            logger.warning(f"Failed to toggle agent runner ({action}): {e}")
            return False


def create_tray_image(width: int = 64, height: int = 64):
    """Draw a vibrant high-resolution OmbiSub system tray icon."""
    if not HAS_PYSTRAY or Image is None:
        return None
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # Background rounded rectangle: deep violet-blue
    draw.rounded_rectangle((2, 2, width - 2, height - 2), radius=14, fill=(107, 70, 193, 255))
    # Subtitle bar symbol in bright cyan/white
    draw.rounded_rectangle((10, 16, width - 10, 28), radius=5, fill=(240, 240, 255, 255))
    draw.rounded_rectangle((14, 36, width - 14, 48), radius=5, fill=(56, 178, 172, 255))
    return image


class TrayApp:
    """System Tray interface managing supervisor actions and menu updates."""

    def __init__(self, supervisor: ServerSupervisor, data_dir: Path):
        self.supervisor = supervisor
        self.data_dir = data_dir
        self.log_dir = data_dir / "logs"
        self.icon: Optional[Any] = None
        self.status_title = f"OmbiSub — Initializing (:{supervisor.port})"

    def _update_status_tooltip(self) -> None:
        """Periodic background status poller updating tray tooltip text."""
        while not self.supervisor.stopping:
            health = self.supervisor.check_health()
            if self.supervisor.is_healthy:
                runner_stat = (health.get("agent_runner") or {}).get("status", "ready")
                q_len = (health.get("queue") or {}).get("pending", 0)
                self.status_title = f"OmbiSub — Running (:{self.supervisor.port}) | Runner: {runner_stat} | Queue: {q_len}"
            else:
                self.status_title = f"OmbiSub — Starting / Offline (:{self.supervisor.port})"

            if self.icon:
                self.icon.title = self.status_title
            time.sleep(10.0)

    def open_web_ui(self) -> None:
        webbrowser.open(f"http://localhost:{self.supervisor.port}")

    def check_updates(self) -> None:
        webbrowser.open(f"http://localhost:{self.supervisor.port}/settings?tab=updates")

    def open_logs_folder(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(self.log_dir))
        else:
            webbrowser.open(f"file://{self.log_dir}")

    def restart_server_action(self) -> None:
        threading.Thread(target=self.supervisor.restart_server, daemon=True).start()

    def toggle_runner_action(self) -> None:
        threading.Thread(target=self.supervisor.toggle_agent_runner, daemon=True).start()

    def exit_action(self) -> None:
        logger.info("Exit requested from tray menu.")
        if self.icon:
            self.icon.stop()
        self.supervisor.stop_server()

    def run(self) -> None:
        """Run the system tray app, or headless fallback if pystray unavailable."""
        self.supervisor.start_server()

        # Start supervisor monitoring thread
        mon_t = threading.Thread(target=self.supervisor.supervisor_loop, daemon=True)
        mon_t.start()

        if not HAS_PYSTRAY:
            logger.info("Running in headless supervisor mode (pystray not installed). Press Ctrl+C to exit.")
            try:
                while not self.supervisor.stopping:
                    time.sleep(1.0)
            except KeyboardInterrupt:
                self.supervisor.stop_server()
            return

        # Start tooltip status poller thread
        poll_t = threading.Thread(target=self._update_status_tooltip, daemon=True)
        poll_t.start()

        # Build pystray Menu
        menu = pystray.Menu(
            pystray.MenuItem(lambda text: self.status_title, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open OmbiSub Web UI", lambda: self.open_web_ui(), default=True),
            pystray.MenuItem("Pause / Resume Agent Runner", lambda: self.toggle_runner_action()),
            pystray.MenuItem("Check for Updates", lambda: self.check_updates()),
            pystray.MenuItem("Open Logs Folder", lambda: self.open_logs_folder()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Restart Server", lambda: self.restart_server_action()),
            pystray.MenuItem("Exit OmbiSub", lambda: self.exit_action()),
        )

        icon_image = create_tray_image()
        self.icon = pystray.Icon("OmbiSub", icon_image, "OmbiSub", menu=menu)
        self.icon.run()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="OmbiSub System Tray App & Supervisor.")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT") or DEFAULT_PORT))
    parser.add_argument("--data-dir", default=None, help="OmbiSub data directory.")
    parser.add_argument("--headless", action="store_true", help="Run windowless supervisor without GUI tray icon.")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir or os.environ.get("OMNISUB_DATA_DIR") or BACKEND_DIR).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["OMNISUB_DATA_DIR"] = str(data_dir)

    lock_file = data_dir / LOCK_NAME
    if not acquire_tray_lock(lock_file, args.port):
        print(f"OmbiSub Tray is already running for {data_dir}.")
        return 0

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(str(data_dir / "logs" / "tray_app.log"), encoding="utf-8"),
        ],
    )

    supervisor = ServerSupervisor(port=args.port, data_dir=data_dir)

    def handle_signal(sig, frame):
        logger.info(f"Signal {sig} received, exiting...")
        supervisor.stop_server()
        release_tray_lock(lock_file)
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        if args.headless:
            # Force headless
            global HAS_PYSTRAY
            HAS_PYSTRAY = False

        app = TrayApp(supervisor, data_dir)
        app.run()
    finally:
        supervisor.stop_server()
        release_tray_lock(lock_file)

    return 0


if __name__ == "__main__":
    sys.exit(main())
