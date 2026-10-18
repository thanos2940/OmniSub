"""
OmbiSub Auto-Update & Version Service
=====================================
Manages Git-based updates and GitHub synchronization for OmbiSub.

Features:
- Checks local HEAD against remote GitHub repository (origin/main).
- Returns changelog, commit comparison, and update status.
- Safe update execution: guards against interrupting active translation jobs.
- Pulls updates (git fetch & reset), updates dependencies (pip install),
  and triggers graceful detached server restart.
- Background polling loop and GitHub push webhook integration.
"""

import os
import sys
import time
import json
import logging
import asyncio
import subprocess
import threading
from pathlib import Path
from typing import Dict, List, Optional, Any
from urllib.request import Request, urlopen
from urllib.error import URLError

from utils import storage

logger = logging.getLogger("omnisub.update_service")

# Resolve repository root and directories
# backend/services/update_service.py -> backend/services -> backend -> root
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
PYTHON_EXE = ROOT_DIR / ".venv" / "Scripts" / "python.exe"
if not PYTHON_EXE.is_file():
    PYTHON_EXE = Path(sys.executable)

GITHUB_REPO = "thanos2940/OmbiSub---Google-Seminar-Capstone-Project"
DEFAULT_BRANCH = "main"


class UpdateService:
    """Manages update checking, dependency synchronization, and graceful restarts."""

    def __init__(self):
        self._last_check_time: float = 0.0
        self._cached_status: Optional[Dict[str, Any]] = None
        self._cache_ttl_seconds: float = 45.0
        self._updating: bool = False

    def is_git_repo(self) -> bool:
        """Check if root directory is a valid git repository."""
        return (ROOT_DIR / ".git").is_dir()

    def _run_git(self, args: List[str], timeout: float = 15.0) -> subprocess.CompletedProcess:
        """Execute a git command in ROOT_DIR with timeout."""
        return subprocess.run(
            ["git"] + args,
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def get_current_version_info(self) -> Dict[str, Any]:
        """Get current local Git commit information."""
        if not self.is_git_repo():
            return {
                "is_git": False,
                "commit_hash": "unknown",
                "commit_short": "unknown",
                "branch": DEFAULT_BRANCH,
                "message": "",
                "author": "",
                "date": "",
            }

        try:
            # Current commit hash
            p_hash = self._run_git(["rev-parse", "HEAD"], timeout=5.0)
            full_hash = p_hash.stdout.strip() if p_hash.returncode == 0 else "unknown"
            short_hash = full_hash[:7] if full_hash != "unknown" else "unknown"

            # Branch name
            p_branch = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"], timeout=5.0)
            branch = p_branch.stdout.strip() if p_branch.returncode == 0 else DEFAULT_BRANCH

            # Commit details
            p_log = self._run_git(["log", "-1", "--pretty=format:%s|%an|%ad", "--date=short"], timeout=5.0)
            msg, author, date = "", "", ""
            if p_log.returncode == 0 and p_log.stdout.strip():
                parts = p_log.stdout.strip().split("|", 2)
                if len(parts) >= 1:
                    msg = parts[0]
                if len(parts) >= 2:
                    author = parts[1]
                if len(parts) >= 3:
                    date = parts[2]

            return {
                "is_git": True,
                "commit_hash": full_hash,
                "commit_short": short_hash,
                "branch": branch,
                "message": msg,
                "author": author,
                "date": date,
            }
        except Exception as e:
            logger.warning(f"Error reading local Git info: {e}")
            return {
                "is_git": True,
                "commit_hash": "error",
                "commit_short": "error",
                "branch": DEFAULT_BRANCH,
                "message": str(e),
                "author": "",
                "date": "",
            }

    async def check_for_updates(self, force: bool = False) -> Dict[str, Any]:
        """Check if newer commits exist on GitHub remote."""
        now = time.time()
        if not force and self._cached_status and (now - self._last_check_time < self._cache_ttl_seconds):
            return self._cached_status

        local_info = self.get_current_version_info()
        branch = local_info.get("branch") or DEFAULT_BRANCH

        update_available = False
        commits_behind = 0
        changelog: List[Dict[str, str]] = []
        remote_commit_hash = local_info.get("commit_hash", "")
        remote_commit_short = local_info.get("commit_short", "")
        remote_message = ""
        remote_author = ""
        remote_date = ""

        # Strategy 1: Git fetch (preferred)
        git_success = False
        if self.is_git_repo():
            try:
                # Fetch remote updates
                fetch_res = await asyncio.to_thread(self._run_git, ["fetch", "origin", branch], 20.0)
                if fetch_res.returncode == 0:
                    git_success = True
                    # Get remote HEAD commit hash
                    rev_res = self._run_git(["rev-parse", f"origin/{branch}"], timeout=5.0)
                    if rev_res.returncode == 0:
                        remote_commit_hash = rev_res.stdout.strip()
                        remote_commit_short = remote_commit_hash[:7]

                    # Count commits behind
                    count_res = self._run_git(["rev-list", "--count", f"HEAD..origin/{branch}"], timeout=5.0)
                    if count_res.returncode == 0 and count_res.stdout.strip().isdigit():
                        commits_behind = int(count_res.stdout.strip())
                        update_available = commits_behind > 0

                    # Parse changelog if behind
                    if update_available:
                        log_res = self._run_git(
                            ["log", f"HEAD..origin/{branch}", "--pretty=format:%h|%s|%an|%ad", "--date=short"],
                            timeout=5.0
                        )
                        if log_res.returncode == 0 and log_res.stdout.strip():
                            for line in log_res.stdout.strip().splitlines():
                                p = line.split("|", 3)
                                if len(p) >= 2:
                                    changelog.append({
                                        "hash": p[0],
                                        "message": p[1],
                                        "author": p[2] if len(p) > 2 else "",
                                        "date": p[3] if len(p) > 3 else "",
                                    })
                            if changelog:
                                remote_message = changelog[0]["message"]
                                remote_author = changelog[0]["author"]
                                remote_date = changelog[0]["date"]
            except Exception as e:
                logger.warning(f"git fetch failed, will try GitHub API fallback: {e}")

        # Strategy 2: GitHub API fallback if git fetch didn't succeed
        if not git_success:
            try:
                api_url = f"https://api.github.com/repos/{GITHUB_REPO}/commits/{branch}"
                req = Request(api_url, headers={"User-Agent": "OmbiSub-UpdateService"})
                
                def _fetch_api():
                    with urlopen(req, timeout=10.0) as resp:
                        return json.loads(resp.read().decode("utf-8"))

                data = await asyncio.to_thread(_fetch_api)
                remote_commit_hash = data.get("sha", "")
                remote_commit_short = remote_commit_hash[:7] if remote_commit_hash else ""
                commit_obj = data.get("commit", {})
                remote_message = commit_obj.get("message", "").split("\n")[0]
                remote_author = commit_obj.get("author", {}).get("name", "")
                remote_date = commit_obj.get("author", {}).get("date", "")[:10]

                if local_info.get("commit_hash") and remote_commit_hash:
                    if local_info["commit_hash"] != remote_commit_hash:
                        update_available = True
                        commits_behind = 1
                        changelog = [{
                            "hash": remote_commit_short,
                            "message": remote_message,
                            "author": remote_author,
                            "date": remote_date,
                        }]
            except Exception as e:
                logger.warning(f"GitHub API update check fallback failed: {e}")

        status = {
            "update_available": update_available,
            "commits_behind": commits_behind,
            "current_commit": local_info.get("commit_short", ""),
            "current_commit_full": local_info.get("commit_hash", ""),
            "current_message": local_info.get("message", ""),
            "current_date": local_info.get("date", ""),
            "latest_commit": remote_commit_short,
            "latest_commit_full": remote_commit_hash,
            "latest_message": remote_message,
            "latest_author": remote_author,
            "latest_date": remote_date,
            "branch": branch,
            "changelog": changelog,
            "last_checked": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
        }

        self._cached_status = status
        self._last_check_time = now
        return status

    def has_active_jobs(self) -> bool:
        """Check whether translation or sync jobs are currently in-flight."""
        try:
            from utils.jobs_manager import jobs, _TERMINAL
            for j in jobs.values():
                if j.status not in _TERMINAL and j.status in ("running", "in_progress", "pending"):
                    return True
        except Exception:
            pass
        try:
            from services import agent_runner
            if agent_runner.get_runner().status().running:
                return True
        except Exception:
            pass
        return False

    async def apply_update(self, force: bool = False) -> Dict[str, Any]:
        """Safely pull latest updates, update dependencies, and trigger restart."""
        if self._updating:
            return {"success": False, "status": "already_updating", "message": "Update is already in progress."}

        # Guard: In-flight job check
        if not force and self.has_active_jobs():
            return {
                "success": False,
                "status": "busy",
                "message": "Cannot update while a translation job is running. Update postponed until queue is idle.",
            }

        if not self.is_git_repo():
            return {
                "success": False,
                "status": "not_git",
                "message": "OmbiSub directory is not a Git repository. Automatic updates require a Git clone.",
            }

        # The update is a `git reset --hard`, which silently destroys uncommitted
        # edits to tracked files. That is fine for a deployed clone and disastrous
        # for a working checkout, so refuse — even when forced; `force` only means
        # "don't wait for running jobs".
        status_res = await asyncio.to_thread(
            self._run_git, ["status", "--porcelain"], 15.0
        )
        if status_res.returncode == 0 and status_res.stdout.strip():
            changed = len(status_res.stdout.strip().splitlines())
            logger.warning(
                f"Update not applied: {changed} file(s) have local changes a reset would erase."
            )
            if not force:
                return {
                    "success": False,
                    "status": "skipped",
                    "reason": "dirty_working_tree",
                    "message": "Working tree has uncommitted changes; commit or stash before updating.",
                }
            return {
                "success": False,
                "status": "local_changes",
                "reason": "dirty_working_tree",
                "message": (
                    f"{changed} file(s) have uncommitted changes that an update would erase. "
                    "Commit or stash them, then update."
                ),
            }

        self._updating = True
        try:
            branch = self.get_current_version_info().get("branch") or DEFAULT_BRANCH

            # Step 1: Fetch and reset to origin
            logger.info(f"Applying update: pulling from origin/{branch}...")
            fetch_res = await asyncio.to_thread(self._run_git, ["fetch", "origin", branch], 30.0)
            if fetch_res.returncode != 0:
                self._updating = False
                return {
                    "success": False,
                    "status": "fetch_failed",
                    "message": f"git fetch failed: {fetch_res.stderr.strip()}",
                }

            # Commits made here and never pushed would vanish in the reset too.
            ahead_res = await asyncio.to_thread(
                self._run_git, ["rev-list", "--count", f"origin/{branch}..HEAD"], 10.0
            )
            ahead = ahead_res.stdout.strip() if ahead_res.returncode == 0 else ""
            if ahead.isdigit() and int(ahead) > 0:
                self._updating = False
                logger.warning(f"Update not applied: {ahead} local commit(s) are not on origin/{branch}.")
                return {
                    "success": False,
                    "status": "local_commits",
                    "message": (
                        f"{ahead} local commit(s) are not on origin/{branch}; an update would discard "
                        "them. Push them first, then update."
                    ),
                }

            # Pre-check if requirements.txt will change
            diff_res = await asyncio.to_thread(
                self._run_git,
                ["diff", f"HEAD..origin/{branch}", "--", "backend/requirements.txt"],
                10.0
            )
            reqs_changed = bool(diff_res.stdout.strip()) if diff_res.returncode == 0 else False

            # Hard reset to remote HEAD (safe for data/ and config.json as they are .gitignored)
            if not force:
                pre_reset_status = await asyncio.to_thread(self._run_git, ["status", "--porcelain"], 15.0)
                if pre_reset_status.returncode == 0 and pre_reset_status.stdout.strip():
                    self._updating = False
                    return {
                        "success": False,
                        "status": "skipped",
                        "reason": "dirty_working_tree",
                        "message": "Working tree has uncommitted changes; commit or stash before updating.",
                    }

            reset_res = await asyncio.to_thread(self._run_git, ["reset", "--hard", f"origin/{branch}"], 20.0)
            if reset_res.returncode != 0:
                self._updating = False
                return {
                    "success": False,
                    "status": "reset_failed",
                    "message": f"git reset failed: {reset_res.stderr.strip()}",
                }

            # Step 2: Install updated requirements if changed
            if reqs_changed:
                logger.info("backend/requirements.txt changed; running pip install...")
                try:
                    pip_cmd = [str(PYTHON_EXE), "-m", "pip", "install", "-r", "requirements.txt"]
                    await asyncio.to_thread(
                        subprocess.run,
                        pip_cmd,
                        cwd=str(BACKEND_DIR),
                        capture_output=True,
                        timeout=180.0,
                        check=False,
                    )
                except Exception as e:
                    logger.warning(f"pip install completed with warning: {e}")

            # Invalidate cached status
            self._cached_status = None
            self._last_check_time = 0.0

            # Step 3: Trigger graceful detached restart
            logger.info("Update files applied successfully. Triggering server restart...")
            self.trigger_restart(delay_seconds=1.5)

            return {
                "success": True,
                "status": "restarting",
                "message": "Update installed successfully. OmbiSub is now restarting...",
            }
        except Exception as e:
            self._updating = False
            logger.error(f"Failed to apply update: {e}", exc_info=True)
            return {"success": False, "status": "error", "message": str(e)}

    def trigger_restart(self, delay_seconds: float = 1.0):
        """Spawn the detached restart helper script and exit."""
        helper_script = ROOT_DIR / "scripts" / "restart_helper.py"
        if not helper_script.is_file():
            logger.error(f"Restart helper script not found at {helper_script}")
            return

        parent_pid = os.getpid()
        cmd = [
            str(PYTHON_EXE),
            str(helper_script),
            "--pid", str(parent_pid),
            "--backend-dir", str(BACKEND_DIR),
            "--python-exe", str(PYTHON_EXE),
            "--port", "8000",
            "--service-name", "OmbiSub",
        ]

        flags = 0
        if sys.platform == "win32":
            # DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP ensures restarter survives parent shutdown
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008  # DETACHED_PROCESS

        try:
            subprocess.Popen(
                cmd,
                cwd=str(ROOT_DIR),
                creationflags=flags,
                close_fds=True,
            )
            logger.info(f"Detached restart helper launched for PID {parent_pid}.")
            threading.Timer(0.5, lambda: os._exit(0)).start()
        except Exception as e:
            logger.error(f"Failed to launch restart helper: {e}")


# Singleton instance
update_service = UpdateService()
