"""Production entry point for OmbiSub (24/7 server use).

    python backend/run_server.py [--host 0.0.0.0] [--port 8000] [--data-dir D:/OmbiSub/data] [--check]

* one uvicorn worker, no reload, graceful shutdown;
* console + rotating file log (<data dir>/logs/ombisub.log, 10 MB x 5);
* honours X-Forwarded-* headers from the ``trusted_proxies`` setting (default 127.0.0.1: a Caddy on this machine);
* refuses to start twice for the same data directory (lock file with stale detection);
* ``--check`` prints the resolved configuration and exits 0 (ready) / 1 (problem) without serving.
"""
import argparse
import json
import logging
import os
import shutil
import socket
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import SimpleNamespace

BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_PORT = 8000
LOCK_NAME = "ombisub_server.lock"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        pass
    if os.name == "nt":  # os.kill(pid, 0) would terminate the process on Windows
        import ctypes
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


def _read_lock(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def acquire_lock(data_dir: Path, port: int):
    """Create the lock file. Returns (path, None) on success, (None, message) when another server owns it."""
    path = data_dir / LOCK_NAME
    payload = json.dumps({"pid": os.getpid(), "port": port,
                          "started": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    for _ in range(3):
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            info = _read_lock(path)
            try:
                pid = int(info.get("pid") or 0)
            except (TypeError, ValueError):
                pid = 0
            if pid and _pid_alive(pid):
                return None, (f"Another OmbiSub server (PID {pid}, port {info.get('port', '?')}, started "
                              f"{info.get('started', '?')}) is already running for the data folder {data_dir}. "
                              f"Stop it first (or use a different --data-dir).")
            try:  # stale: the owner died without cleaning up
                path.unlink()
            except OSError:
                time.sleep(0.2)
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        return path, None
    return None, f"Could not take the lock file {path}."


def release_lock(path) -> None:
    try:
        if path and int(_read_lock(path).get("pid") or 0) == os.getpid():
            path.unlink()
    except (OSError, TypeError, ValueError):
        pass


def trusted_proxies(config: dict) -> str:
    """The ``trusted_proxies`` setting (comma separated addresses, "*" = any) for uvicorn; default: this machine."""
    value = str((config or {}).get("trusted_proxies") or "").strip()
    return value or "127.0.0.1"


def setup_logging(data_dir: Path) -> Path:
    logs_dir = data_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(console)
    # Same file name main.py uses, installed first so main.py's own check finds a RotatingFileHandler
    # and adds no second one.
    fh = RotatingFileHandler(str(logs_dir / "ombisub.log"), maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    fh.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(fh)
    # A log that runs for months keeps only what matters: every Sonarr/Radarr poll (httpx) and
    # every request of an open browser tab (uvicorn.access) would bury it. Webhooks log themselves.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    return logs_dir / "ombisub.log"


def _ffmpeg_tools():
    """ffmpeg/ffprobe the way the extractor finds them: the ffmpeg_path setting, then PATH. None if missing."""
    try:
        from utils import storage
        from integrations.embedded_subs import resolve_tools
        return resolve_tools(storage.load_global_config())
    except Exception:  # the check must not crash on an import problem
        ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
        return SimpleNamespace(ffmpeg=ffmpeg, ffprobe=ffprobe) if ffmpeg and ffprobe else None


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host or "0.0.0.0", port))
            return True
        except OSError:
            return False


def run_check(data_dir: Path, host: str, port: int) -> int:
    problems = []
    print("OmbiSub server check")
    print(f"  python        : {sys.version.split()[0]} ({sys.executable})")
    if sys.version_info < (3, 11):
        problems.append("Python 3.11+ is required")
    print(f"  data dir      : {data_dir}")
    cfg = data_dir / "config.json"
    print(f"  config.json   : {cfg}  ({'found' if cfg.is_file() else 'not yet created'})")
    print(f"  log file      : {data_dir / 'logs' / 'ombisub.log'}")
    lock = data_dir / LOCK_NAME
    info = _read_lock(lock) if lock.exists() else {}
    try:
        held = bool(info) and _pid_alive(int(info.get("pid") or 0))
    except (TypeError, ValueError):
        held = False
    if held:
        print(f"  lock          : HELD by PID {info.get('pid')} (port {info.get('port')})")
        problems.append("another server already runs for this data folder")
    else:
        print("  lock          : free" + (" (a stale lock file will be replaced)" if info else ""))
        if not _port_free(host, port):
            problems.append(f"port {port} is in use")
    print(f"  listen        : {host}:{port}  ({'port in use' if any('in use' in p for p in problems) else 'ok'})")
    index = BACKEND_DIR / "static" / "index.html"
    print(f"  web UI build  : {'present' if index.is_file() else 'MISSING (warning: build the UI)'}  ({index})")
    tools = _ffmpeg_tools()
    missing = "NOT found (warning: set ffmpeg_path in Settings or put ffmpeg on PATH; embedded subtitles cannot be read)"
    print(f"  ffmpeg        : {tools.ffmpeg if tools else missing}")
    print(f"  ffprobe       : {tools.ffprobe if tools else missing}")
    try:
        from services import agent_cli
        status = agent_cli.engine_status()
        for name, cmd in status.items():
            print(f"  agent {name:<7} : {cmd or 'not found'}")
        if not (status.get("antigravity") or status.get("claude")):
            print("  warning       : neither agy nor claude was found - the agent runner cannot translate")
    except Exception as exc:  # the check must not crash on an import problem
        print(f"  agent CLIs    : could not check ({exc})")
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
        print("  packages      : fastapi + uvicorn importable")
    except ImportError as exc:
        problems.append(f"missing package: {exc}")
    print("RESULT: " + ("OK" if not problems else "PROBLEMS - " + "; ".join(problems)))
    return 0 if not problems else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the OmbiSub server (production).")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT") or DEFAULT_PORT))
    ap.add_argument("--data-dir", default=None, help="Folder for config, databases, projects and logs (OMNISUB_DATA_DIR).")
    ap.add_argument("--check", action="store_true", help="Print the resolved setup and exit (0 = ready, 1 = problem).")
    args = ap.parse_args(argv)

    if args.data_dir:  # must be set before anything imports utils.paths
        os.environ["OMNISUB_DATA_DIR"] = str(Path(args.data_dir).expanduser().resolve())
    data_dir = Path(os.environ.get("OMNISUB_DATA_DIR") or BACKEND_DIR).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["OMNISUB_DATA_DIR"] = str(data_dir)
    os.environ["OMBISUB_PORT"] = str(args.port)  # read by the updater's restart and agent tools
    os.chdir(BACKEND_DIR)
    sys.path.insert(0, str(BACKEND_DIR))

    if args.check:
        return run_check(data_dir, args.host, args.port)

    log_file = setup_logging(data_dir)
    log = logging.getLogger("omnisub.server")
    lock, problem = acquire_lock(data_dir, args.port)
    if problem:
        log.error(problem)
        return 2
    try:
        if not (BACKEND_DIR / "static" / "index.html").is_file():
            log.warning("The web UI is not built (backend/static/index.html is missing). The API works, but there is "
                        "no UI. Build it: cd frontend && npm ci && npm run build, then copy frontend/dist to "
                        "backend/static (deploy/windows/install.ps1 does this).")
        if not _ffmpeg_tools():
            log.warning("ffmpeg/ffprobe were not found (ffmpeg_path setting or PATH): embedded subtitle tracks "
                        "cannot be read or extracted.")
        log.info("Starting OmbiSub on %s:%s (data dir %s, log %s, PID %s)", args.host, args.port, data_dir, log_file, os.getpid())

        import uvicorn
        from main import app
        try:  # same startup sync main.py's __main__ block does
            from utils.rate_limiter import per_model_rate_limiter
            per_model_rate_limiter.load_config_and_state()
        except Exception as exc:
            log.warning("Rate limiter state not loaded: %s", exc)
        from utils import storage
        config = uvicorn.Config(app, host=args.host, port=args.port, workers=1, reload=False,
                                log_config=None, timeout_graceful_shutdown=20,
                                proxy_headers=True,  # X-Forwarded-For/-Proto from a reverse proxy (Caddy) ...
                                forwarded_allow_ips=trusted_proxies(storage.load_global_config()))  # ... if it is trusted
        uvicorn.Server(config).run()  # handles Ctrl+C / SIGTERM and runs the lifespan shutdown
        log.info("OmbiSub stopped.")
        return 0
    finally:
        release_lock(lock)


if __name__ == "__main__":
    sys.exit(main())
