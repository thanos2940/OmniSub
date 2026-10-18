"""
OmbiSub Detached Restart Helper
===============================
This script is launched detached from the main OmbiSub server process to perform
a safe, graceful restart after an update has been applied.

It handles two deployment modes:
1. Windows Service: Restarts the service via PowerShell / NSSM.
2. Console / Standalone Process: Waits for the previous process to terminate,
   verifies port release, and relaunches the Uvicorn server.
"""

import sys
import os
import time
import socket
import logging
import argparse
import subprocess
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# Setup logging to logs/restart.log
ROOT_DIR = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
RESTART_LOG = LOGS_DIR / "restart.log"

logging.basicConfig(
    filename=str(RESTART_LOG),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("restart_helper")


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Check if a TCP port is currently occupied."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is still alive."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            out = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                text=True,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW
            )
            return str(pid) in out
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def is_windows_service_running(service_name: str) -> bool:
    """Check if a Windows service exists and is running."""
    if sys.platform != "win32":
        return False
    try:
        out = subprocess.check_output(
            ["sc.exe", "query", service_name],
            text=True,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW
        )
        return "RUNNING" in out or "START_PENDING" in out
    except Exception:
        return False


def restart_windows_service(service_name: str) -> bool:
    """Restart the Windows service using PowerShell / NSSM."""
    logger.info(f"Restarting Windows Service '{service_name}'...")
    try:
        cmd = ["powershell.exe", "-NoProfile", "-Command", f"Restart-Service -Name '{service_name}' -Force"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30, creationflags=CREATE_NO_WINDOW)
        if res.returncode == 0:
            logger.info(f"Successfully restarted Windows service '{service_name}'.")
            return True
        logger.warning(f"PowerShell Restart-Service returned {res.returncode}: {res.stderr}")
    except Exception as e:
        logger.warning(f"Failed to restart via PowerShell: {e}")

    # Fallback to net stop / net start
    try:
        subprocess.run(["net", "stop", service_name], capture_output=True, text=True, timeout=15, creationflags=CREATE_NO_WINDOW)
        time.sleep(2)
        subprocess.run(["net", "start", service_name], capture_output=True, text=True, timeout=15, creationflags=CREATE_NO_WINDOW)
        logger.info(f"Service restarted via net stop/start.")
        return True
    except Exception as e:
        logger.error(f"Failed net stop/start: {e}")
        return False


def restart_standalone(python_exe: str, backend_dir: str, port: int, host: str = "0.0.0.0"):
    """Relaunch OmbiSub in a standalone console window."""
    logger.info(f"Relaunching OmbiSub standalone using Python: {python_exe} in {backend_dir}...")

    cmd = [python_exe, "-m", "uvicorn", "main:app", "--host", host, "--port", str(port)]
    
    flags = CREATE_NO_WINDOW
    if sys.platform == "win32":
        # CREATE_NEW_CONSOLE or CREATE_NO_WINDOW based on environment
        flags = subprocess.CREATE_NEW_CONSOLE | CREATE_NO_WINDOW

    try:
        subprocess.Popen(
            cmd,
            cwd=backend_dir,
            creationflags=flags,
            close_fds=True,
        )
        logger.info("Successfully spawned new OmbiSub server process.")
    except Exception as e:
        logger.error(f"Failed to spawn new server process: {e}")


def main():
    parser = argparse.ArgumentParser(description="OmbiSub Detached Restarter")
    parser.add_argument("--pid", type=int, default=0, help="Parent PID to wait for exit")
    parser.add_argument("--port", type=int, default=8000, help="Server port to wait for release")
    parser.add_argument("--backend-dir", type=str, default="", help="Backend working directory")
    parser.add_argument("--python-exe", type=str, default="", help="Python interpreter executable")
    parser.add_argument("--service-name", type=str, default="OmbiSub", help="Windows service name")
    args = parser.parse_args()

    backend_dir = args.backend_dir or str(ROOT_DIR / "backend")
    python_exe = args.python_exe or sys.executable

    logger.info(f"--- Restart helper started (PID={os.getpid()}, Parent PID={args.pid}, Port={args.port}) ---")

    # If running as a Windows Service, restart via service manager
    if is_windows_service_running(args.service_name):
        logger.info(f"Detected running Windows Service '{args.service_name}'.")
        # Give parent process 1 second to finish responding to the client
        time.sleep(1)
        restart_windows_service(args.service_name)
        return

    # Console / Standalone mode:
    # 1. Wait for parent process to exit
    if args.pid > 0:
        logger.info(f"Waiting for parent process (PID {args.pid}) to exit...")
        deadline = time.time() + 15.0
        while time.time() < deadline:
            if not is_pid_alive(args.pid):
                logger.info(f"Parent process {args.pid} has exited.")
                break
            time.sleep(0.5)

    # 2. Wait for port to be released
    logger.info(f"Waiting for port {args.port} to be released...")
    port_deadline = time.time() + 15.0
    while time.time() < port_deadline:
        if not is_port_in_use(args.port):
            logger.info(f"Port {args.port} is now free.")
            break
        time.sleep(0.5)

    time.sleep(1)

    # 3. Relaunch server
    restart_standalone(python_exe, backend_dir, args.port)
    logger.info("Restart sequence completed.")


if __name__ == "__main__":
    main()
