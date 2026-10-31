"""
OmbiSub Auto-Update & Official GitHub Releases Service
=====================================================
Manages official GitHub release tracking, dependency synchronization,
frontend static rebuilding, and graceful server restarts.

Features:
- Two update channels (setting ``update_channel``): ``main`` follows origin/main (every pushed commit is
  an update, the commit subjects are the release notes) and ``releases`` follows GitHub Releases
  (intermediate commits never trigger an update). ``auto`` (the default) is ``main`` while the repository
  has no release tags and ``releases`` once it has.
- Safe update execution: guards against interrupting active translation jobs
  and refuses to erase uncommitted or unpushed local changes.
- An update that finds OmbiSub busy (a GitHub push, a scheduled check, Install pressed during work) waits
  for it: one waiter task installs at the first moment nothing is in progress. A server that stays busy
  holds new work after a while (services/work_hold.py, in memory only) so the work in hand can finish;
  nothing in progress is ever cut off for an update.
- Automatically updates Python dependencies (pip install) and frontend static
  assets (static.zip or npm build) on release diffs.
- Supports detached restart helper and tray supervisor code 42 exit.
"""

import os
import sys
import time
import json
import io
import shutil
import logging
import asyncio
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from utils import storage
from utils.paths import DATA_DIR
from services import work_hold

logger = logging.getLogger("omnisub.update_service")

# Resolve repository root and directories
# backend/services/update_service.py -> backend/services -> backend -> root
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
FRONTEND_DIR = ROOT_DIR / "frontend"
PYTHON_EXE = ROOT_DIR / ".venv" / "Scripts" / "python.exe"
if not PYTHON_EXE.is_file():
    PYTHON_EXE = Path(sys.executable)

GITHUB_REPO = "thanos2940/OmbiSub---Google-Seminar-Capstone-Project"
DEFAULT_BRANCH = "main"
CHANNEL_MAIN = "main"
CHANNEL_RELEASES = "releases"
UPDATE_CHANNELS = ("auto", CHANNEL_MAIN, CHANNEL_RELEASES)
MAX_CHANGELOG = 20  # commit subjects shown as the release notes of the main channel

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
RELEASE_VERSION_FILE = DATA_DIR / "release_version.json"
# The result of the last update check. The status endpoint (called by the UI on every page load) only
# reads this; the network is touched by an explicit check, the auto-update loop, or a background refresh
# when the result is older than CHECK_MAX_AGE_SECONDS (GitHub's API rate-limits anonymous callers hard).
CHECK_CACHE_FILE = DATA_DIR / "update_check.json"
CHECK_MAX_AGE_SECONDS = 6 * 3600
CHECK_RETRY_SECONDS = 600  # minimum gap between two background refreshes, successful or not

# An update deferred because work is in progress (request_update_when_idle).
IDLE_CHECK_SECONDS = 30          # how often the waiting update looks for a moment with nothing in progress
HOLD_AFTER_SECONDS = 10 * 60     # still busy after this long: new work is held so the work in hand can finish
HOLD_MAX_SECONDS = 2 * 3600      # held this long and still busy (something never ends): the hold is lifted
RETRY_SECONDS = 600              # an install that failed for a passing reason (git fetch) is tried again after this
MAX_INSTALL_ATTEMPTS = 3
RETRYABLE_FAILURES = ("fetch_failed", "reset_failed", "error")
HOLD_MESSAGE = ("An update is waiting to be installed: the work in hand is finished first, and new work starts "
                "after OmbiSub restarts on the new version.")
INSTALL_MESSAGE = "Installing an update: new work starts after OmbiSub restarts on the new version."
BUSY_SHOWN = 3                   # in-progress items named in a message; the rest are counted


def _iso(ts: Optional[float]) -> Optional[str]:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if ts else None


def _span(seconds: float) -> str:
    """A duration in the words of a message: "2 hours", "10 minutes"."""
    minutes = round(seconds / 60)
    if minutes >= 60 and minutes % 60 == 0:
        return f"{minutes // 60} hour{'s' if minutes != 60 else ''}"
    if minutes >= 1:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    return f"{max(1, round(seconds))} seconds"


def _clock(ts: float) -> str:
    """The server's local time of day, for messages (the UI formats the ISO times itself)."""
    return datetime.fromtimestamp(ts).strftime("%H:%M")


class UpdateService:
    """Manages update checking, dependency synchronization, and graceful restarts."""

    RELEASE_VERSION_FILE = RELEASE_VERSION_FILE
    CHECK_CACHE_FILE = CHECK_CACHE_FILE

    def __init__(self):
        self._cached_status: Optional[Dict[str, Any]] = None
        self._last_attempt: float = 0.0
        self._refresh_task: Optional["asyncio.Task"] = None
        self._updating: bool = False
        self._pending: Optional[Dict[str, Any]] = None        # the deferred update, while one waits
        self._waiter: Optional["asyncio.Task"] = None          # the one task that installs it
        self._wake: Optional["asyncio.Event"] = None           # set by a new request: the waiter looks now
        self._last_deferred: Optional[Dict[str, Any]] = None  # how the last one ended without a restart
        self.github_repo: str = GITHUB_REPO
        self.backend_dir: Path = BACKEND_DIR
        self.frontend_dir: Path = FRONTEND_DIR
        self.static_dir: Path = BACKEND_DIR / "static"

    def is_git_repo(self) -> bool:
        """Check if root directory is a valid git repository."""
        return (ROOT_DIR / ".git").exists()  # a directory, or a file in a git worktree

    def _run_git(self, args: List[str], timeout: float = 15.0) -> subprocess.CompletedProcess:
        """Execute a git command in ROOT_DIR with timeout."""
        return subprocess.run(
            ["git"] + args,
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            creationflags=CREATE_NO_WINDOW,
        )

    def resolve_channel(self, release_only: Optional[bool] = None) -> str:
        """The channel in force: ``release_only`` (True = releases, False = main) wins, then the
        ``update_channel`` setting; ``auto`` means releases once the repository has release tags."""
        if release_only is not None:
            return CHANNEL_RELEASES if release_only else CHANNEL_MAIN
        configured = str(storage.load_global_config().get("update_channel") or "auto").strip().lower()
        if configured in (CHANNEL_MAIN, CHANNEL_RELEASES):
            return configured
        if self.is_git_repo():
            tags = self._run_git(["tag", "--list"], timeout=5.0)
            if tags.returncode == 0 and str(tags.stdout).strip():
                return CHANNEL_RELEASES
        return CHANNEL_MAIN

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

    def get_installed_release(self) -> Dict[str, Any]:
        """Get installed release metadata from release_version.json or git tags fallback."""
        if self.RELEASE_VERSION_FILE.is_file():
            try:
                data = json.loads(self.RELEASE_VERSION_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "tag_name" in data:
                    return {
                        "tag_name": data.get("tag_name") or "untagged",
                        "commit_sha": data.get("commit_sha") or "",
                        "installed_at": data.get("installed_at") or "",
                    }
            except Exception as e:
                logger.warning(f"Failed parsing release_version.json: {e}")

        # Git fallback
        tag_name = "untagged"
        commit_sha = ""
        if self.is_git_repo():
            p_tag = self._run_git(["describe", "--tags", "--exact-match", "HEAD"], timeout=5.0)
            if p_tag.returncode == 0 and p_tag.stdout.strip():
                tag_name = p_tag.stdout.strip()
            else:
                p_desc = self._run_git(["describe", "--tags", "HEAD"], timeout=5.0)
                if p_desc.returncode == 0 and p_desc.stdout.strip():
                    tag_name = p_desc.stdout.strip()

            local_info = self.get_current_version_info()
            commit_sha = local_info.get("commit_hash", "")

        return {
            "tag_name": tag_name,
            "commit_sha": commit_sha,
            "installed_at": "",
        }

    def fetch_latest_github_release(self) -> Optional[Dict[str, Any]]:
        """Query official GitHub release via API with git ls-remote tag fallback."""
        api_url = f"https://api.github.com/repos/{self.github_repo}/releases/latest"
        req = Request(
            api_url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "OmbiSub-Updater",
            }
        )

        try:
            with urlopen(req, timeout=10.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return {
                    "tag_name": data.get("tag_name"),
                    "name": data.get("name") or data.get("tag_name"),
                    "body": data.get("body") or "",
                    "published_at": data.get("published_at"),
                    "html_url": data.get("html_url"),
                    "prerelease": bool(data.get("prerelease")),
                    "draft": bool(data.get("draft")),
                    "assets": data.get("assets") or [],
                    "target_commitish": data.get("target_commitish") or "",
                }
        except HTTPError as e:
            if e.code == 404:
                logger.info("GitHub returned 404: no releases published yet for repository.")
                return None
            logger.warning(f"GitHub API release check returned HTTP {e.code}: {e.reason}")
        except Exception as e:
            logger.warning(f"GitHub release API fetch failed: {e}")

        # Fallback to checking git remote tags
        try:
            p_tags = self._run_git(["ls-remote", "--tags", "--refs", "--sort=-v:refname", "origin"], timeout=15.0)
            if p_tags.returncode == 0 and p_tags.stdout.strip():
                lines = [l.strip() for l in p_tags.stdout.strip().splitlines() if l.strip()]
                for line in lines:
                    parts = line.split()
                    if len(parts) >= 2:
                        ref = parts[1]
                        tag = ref.replace("refs/tags/", "")
                        return {
                            "tag_name": tag,
                            "name": tag,
                            "body": "",
                            "published_at": "",
                            "html_url": f"https://github.com/{self.github_repo}/releases/tag/{tag}",
                            "prerelease": False,
                            "draft": False,
                            "assets": [],
                            "target_commitish": parts[0],
                        }
        except Exception as e:
            logger.warning(f"Git tags fallback failed: {e}")

        return None

    async def check_for_updates(self, force: bool = False, release_only: Optional[bool] = None) -> Dict[str, Any]:
        """Check for updates using Official GitHub Releases or Git commit fallback."""
        now = time.time()
        self._last_attempt = now
        if not force:
            cached = self.cached_status()
            if cached.get("checked") and now - cached.get("checked_at", 0) < CHECK_MAX_AGE_SECONDS:
                return cached

        local_info = self.get_current_version_info()
        branch = local_info.get("branch") or DEFAULT_BRANCH
        installed_release = self.get_installed_release()

        channel = self.resolve_channel(release_only)
        use_releases = channel == CHANNEL_RELEASES
        latest_release = await asyncio.to_thread(self.fetch_latest_github_release) if use_releases else None

        # -------------------------------------------------------------
        # Mode A: Official GitHub Releases
        # -------------------------------------------------------------
        if use_releases:
            update_available = False
            release_status = "up_to_date"

            if latest_release and not latest_release.get("draft") and not latest_release.get("prerelease"):
                latest_tag = latest_release.get("tag_name")
                installed_tag = installed_release.get("tag_name")

                # If tags differ, verify tag does not match current commit
                if latest_tag and latest_tag != installed_tag:
                    update_available = True
                    target_sha = latest_release.get("target_commitish")
                    if target_sha and local_info.get("commit_hash") == target_sha:
                        update_available = False

                if update_available:
                    release_status = "new_release_available"
            elif latest_release is None:
                update_available = False
                release_status = "no_releases_published"

            changelog = []
            if update_available and latest_release:
                changelog = [{
                    "hash": latest_release.get("tag_name", ""),
                    "message": latest_release.get("name") or latest_release.get("body", "")[:80],
                    "author": "GitHub Release",
                    "date": (latest_release.get("published_at") or "")[:10],
                }]

            status = {
                "channel": CHANNEL_RELEASES,
                "behind": 1 if update_available else 0,
                "ahead": 0,
                "commits": [],
                "update_available": update_available,
                "release_mode": "github_release",
                "release_status": release_status,
                "latest_release": latest_release,
                "installed_release": installed_release,
                "commits_behind": 1 if update_available else 0,
                "current_commit": local_info.get("commit_short", ""),
                "current_commit_full": local_info.get("commit_hash", ""),
                "current_message": local_info.get("message", ""),
                "current_date": local_info.get("date", ""),
                "latest_commit": (latest_release.get("target_commitish") or "")[:7] if latest_release else local_info.get("commit_short", ""),
                "latest_commit_full": latest_release.get("target_commitish", "") if latest_release else local_info.get("commit_hash", ""),
                "latest_message": latest_release.get("name", "") if latest_release else (
                    "No releases published yet" if latest_release is None else "Up to date"
                ),
                "latest_author": "GitHub Release" if latest_release else "",
                "latest_date": (latest_release.get("published_at") or "")[:10] if latest_release else "",
                "branch": branch,
                "changelog": changelog,
                "last_checked": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            }

            return self._remember(status, now)

        # -------------------------------------------------------------
        # Mode B: the main channel - follow origin/main
        # -------------------------------------------------------------
        remote = DEFAULT_BRANCH  # the main channel always follows origin/main
        update_available = False
        commits_behind = 0
        commits_ahead = 0
        changelog: List[Dict[str, str]] = []
        remote_commit_hash = local_info.get("commit_hash", "")
        remote_commit_short = local_info.get("commit_short", "")
        remote_message = ""
        remote_author = ""
        remote_date = ""

        git_success = False
        if self.is_git_repo():
            try:
                fetch_res = await asyncio.to_thread(self._run_git, ["fetch", "origin", remote], 20.0)
                if fetch_res.returncode == 0:
                    git_success = True
                    rev_res = self._run_git(["rev-parse", f"origin/{remote}"], timeout=5.0)
                    if rev_res.returncode == 0:
                        remote_commit_hash = rev_res.stdout.strip()
                        remote_commit_short = remote_commit_hash[:7]

                    count_res = self._run_git(["rev-list", "--count", f"HEAD..origin/{remote}"], timeout=5.0)
                    if count_res.returncode == 0 and count_res.stdout.strip().isdigit():
                        commits_behind = int(count_res.stdout.strip())
                        update_available = commits_behind > 0

                    ahead_res = self._run_git(["rev-list", "--count", f"origin/{remote}..HEAD"], timeout=5.0)
                    if ahead_res.returncode == 0 and ahead_res.stdout.strip().isdigit():
                        commits_ahead = int(ahead_res.stdout.strip())

                    if update_available:
                        log_res = self._run_git(
                            ["log", f"HEAD..origin/{remote}", f"--max-count={MAX_CHANGELOG}",
                             "--pretty=format:%h|%s|%an|%ad", "--date=short"],
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

        # GitHub API fallback if git fetch didn't succeed
        api_success = False
        if not git_success:
            try:
                api_url = f"https://api.github.com/repos/{self.github_repo}/commits/{remote}"
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
                api_success = True
            except Exception as e:
                logger.warning(f"GitHub API update check fallback failed: {e}")

        status = {
            "channel": CHANNEL_MAIN,
            "behind": commits_behind,
            "ahead": commits_ahead,
            "commits": changelog,
            "update_available": update_available,
            "release_mode": "commits",
            "latest_release": latest_release,
            "installed_release": installed_release,
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

        if not (git_success or api_success):
            # Neither git nor GitHub answered: report it, but do not cache "up to date" as a result.
            status["checked"] = False
            status["error"] = "Could not reach GitHub to check for updates."
            return status
        return self._remember(status, now)

    # ------------------------------------------------------------------ cached check result

    def _remember(self, status: Dict[str, Any], now: float) -> Dict[str, Any]:
        status["checked"] = True
        status["checked_at"] = now
        self._cached_status = status
        try:
            self.CHECK_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.CHECK_CACHE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(status), encoding="utf-8")
            os.replace(tmp, self.CHECK_CACHE_FILE)
        except Exception as e:
            logger.debug(f"Could not persist the update check: {e}")
        return status

    def _forget_check(self) -> None:
        self._cached_status = None
        try:
            self.CHECK_CACHE_FILE.unlink()
        except OSError:
            pass

    def cached_status(self) -> Dict[str, Any]:
        """The last check result, without touching the network. Falls back to a status built from the
        local checkout alone ("checked": False) when there is none, or when it describes another
        commit or channel than the ones in force now."""
        local = self.get_current_version_info()
        channel = self.resolve_channel()
        status = self._cached_status
        if status is None and self.CHECK_CACHE_FILE.is_file():
            try:
                loaded = json.loads(self.CHECK_CACHE_FILE.read_text(encoding="utf-8"))
                status = loaded if isinstance(loaded, dict) else None
            except Exception:
                status = None
        if status and status.get("current_commit_full") == local.get("commit_hash") and status.get("channel") == channel:
            self._cached_status = status
            return status
        commit = local.get("commit_hash", "")
        return {
            "checked": False,
            "checked_at": 0,
            "channel": channel,
            "update_available": False,
            "release_mode": "github_release" if channel == CHANNEL_RELEASES else "commits",
            "release_status": "not_checked",
            "latest_release": None,
            "installed_release": self.get_installed_release(),
            "commits_behind": 0, "behind": 0, "ahead": 0, "commits": [], "changelog": [],
            "current_commit": local.get("commit_short", ""),
            "current_commit_full": commit,
            "current_message": local.get("message", ""),
            "current_date": local.get("date", ""),
            "latest_commit": local.get("commit_short", ""),
            "latest_commit_full": commit,
            "latest_message": "", "latest_author": "", "latest_date": "",
            "branch": local.get("branch") or DEFAULT_BRANCH,
            "last_checked": "",
        }

    def is_stale(self, status: Optional[Dict[str, Any]] = None) -> bool:
        status = status if status is not None else self.cached_status()
        return time.time() - float(status.get("checked_at") or 0) >= CHECK_MAX_AGE_SECONDS

    def schedule_refresh(self) -> bool:
        """Start one background check when the cached result is old (or missing). Single flight, and at
        most one attempt per CHECK_RETRY_SECONDS even when it fails. Never blocks the caller."""
        now = time.time()
        if self._refresh_task is not None and not self._refresh_task.done():
            return False
        if now - self._last_attempt < CHECK_RETRY_SECONDS or not self.is_stale():
            return False
        try:
            self._refresh_task = asyncio.get_running_loop().create_task(self.check_for_updates(force=True))
        except RuntimeError:  # no running loop
            return False
        self._last_attempt = now
        return True

    def busy_reasons(self) -> List[str]:
        """What is in progress right now that the restart of an update would cut off, a few words each: jobs in
        the registry (Gemini translations, extractions, syncs), the agy runner's episodes in hand (a lane that
        waits for quota or is paused has none), Gemini worker tasks whose job is not registered yet, export
        retries and one-off agy requests. Empty: nothing would be lost. Cheap and never blocks (event loop)."""
        out: List[str] = []
        live_jobs = set()
        try:
            from utils.jobs_manager import jobs, _TERMINAL
            for job_id, job in list(jobs.items()):
                if job.status in _TERMINAL or job.status not in ("running", "in_progress", "pending"):
                    continue
                live_jobs.add(job_id)
                what = " ".join(x for x in (job.project_name, job.episode_name) if x) or job_id
                message = (job.message or "").strip()
                out.append(f"{what}: {message[:80]}" if message else what)
        except Exception:
            pass
        try:
            from services import agent_runner
            runner = agent_runner.current_runner()
            if runner is not None:
                out.extend(runner.work_in_hand())
        except Exception:
            pass
        try:
            from services.background_worker import worker
            hand = worker.work_in_hand()
            starting = [t for t in hand["translations"] + hand["extractions"] if t not in live_jobs]
            if starting:
                out.append(f"{len(starting)} Gemini worker task(s) starting")
            if hand["exports"]:
                out.append(f"{len(hand['exports'])} subtitle export(s)")
        except Exception:
            pass
        try:
            from services import agy_llm
            asks = [p for p in agy_llm.in_flight() if p != "bootstrap"]   # a show's bootstrap is a runner lane's
            if asks:
                out.append(f"{len(asks)} agy request(s) ({', '.join(sorted(set(asks)))})")
        except Exception:
            pass
        return out

    def has_active_jobs(self) -> bool:
        """Anything in progress that an update's restart would cut off (``busy_reasons``)."""
        return bool(self.busy_reasons())

    @staticmethod
    def _summary(busy: List[str]) -> str:
        shown = "; ".join(busy[:BUSY_SHOWN])
        return shown + (f"; and {len(busy) - BUSY_SHOWN} more" if len(busy) > BUSY_SHOWN else "")

    # ------------------------------------------------------------------ an update that waits for a gap

    def request_update_when_idle(self, source: str, commit: str = "", hold_now: bool = False,
                                 automatic: bool = True) -> Dict[str, Any]:
        """Install the update at the first moment nothing is in progress, and say what will happen.

        One waiter task serves every request: another push or check while one waits only refreshes it. New work
        is held (services/work_hold.py) once OmbiSub has stayed busy for HOLD_AFTER_SECONDS, or at once with
        ``hold_now`` (a person pressed Install), so the work in hand can finish. A hold that lasts
        HOLD_MAX_SECONDS without the work in hand ending is lifted, and the update then waits for a natural gap.
        Nothing in progress is cut off. ``automatic`` requests (a push, a scheduled check) are dropped when
        automatic updates are switched off while they wait. Call on the event loop."""
        now = time.time()
        p = self._pending
        if p is None:
            p = self._pending = {
                "requested_at": now, "source": source, "commit": commit, "hold_now": hold_now,
                "automatic": automatic, "state": "waiting", "attempts": 0, "next_try": 0.0,
                "hold_token": None, "holding_since": None, "hold_lifted": False, "busy": [], "last_result": None,
            }
            logger.info(f"Update requested ({source}): it is installed at the first moment nothing is in progress.")
        else:
            p["source"] = source
            p["commit"] = commit or p["commit"]
            p["automatic"] = p["automatic"] and automatic   # once a person asked, the setting no longer drops it
            if hold_now:   # a person asked (again): hold at once, even after an earlier hold was lifted
                p["hold_now"], p["hold_lifted"] = True, False
            if p["state"] == "retrying":   # a new push or a person: try again now rather than at next_try
                p["state"], p["next_try"] = "waiting", 0.0
        self._last_deferred = None
        p["busy"] = self.busy_reasons()
        if p["busy"] and p["state"] in ("waiting", "holding"):
            self._pace_hold(p, now)   # Install pressed: the hold starts with the answer, not 30 s later
        self._ensure_waiter()
        return {"status": "deferred" if p["busy"] else "updating", "busy": list(p["busy"]),
                "message": self._describe(p), "pending_update": self.pending_update_info()}

    def pending_update_info(self) -> Optional[Dict[str, Any]]:
        """The update that waits, for the UI: what it waits for, whether new work is held (since when, or from
        when on), the last failed attempt, and one sentence saying all of it. None when none waits."""
        p = self._pending
        if p is None:
            return None
        holding = p["holding_since"] is not None
        hold_at = None
        if not holding and not p["hold_lifted"]:
            hold_at = p["requested_at"] + (0 if p["hold_now"] else HOLD_AFTER_SECONDS)
        return {
            "state": p["state"], "source": p["source"], "commit": p["commit"],
            "requested_at": _iso(p["requested_at"]), "holding": holding,
            "holding_since": _iso(p["holding_since"]), "hold_at": _iso(hold_at), "hold_lifted": p["hold_lifted"],
            "busy": list(p["busy"]), "attempts": p["attempts"],
            "next_try": _iso(p["next_try"]) if p["next_try"] > time.time() else None,
            "last_result": p["last_result"], "cancellable": p["state"] not in ("installing", "restarting"),
            "message": self._describe(p),
        }

    def last_deferred_update(self) -> Optional[Dict[str, Any]]:
        """How the last waiting update ended without a restart (refused, cancelled, nothing to install)."""
        return self._last_deferred

    def cancel_pending_update(self) -> Dict[str, Any]:
        """Drop the waiting update and lift its hold. Refused once the install has begun."""
        p = self._pending
        if p is None:
            return {"cancelled": False, "message": "No update is waiting."}
        if p["state"] in ("installing", "restarting"):
            return {"cancelled": False, "message": "The update is being installed and can no longer be stopped."}
        self._end_pending("cancelled", "Cancelled by hand; nothing was installed.")
        waiter, self._waiter = self._waiter, None
        if waiter is not None and not waiter.done():
            waiter.cancel()
        return {"cancelled": True, "message": "The waiting update was cancelled; new work goes on as usual."}

    def shutdown(self) -> None:
        """Server stop: the waiter ends with the process, and its hold is lifted (it is in memory only anyway)."""
        waiter, self._waiter = self._waiter, None
        if waiter is not None and not waiter.done():
            waiter.cancel()
        if self._pending is not None and self._pending["state"] != "restarting":
            self._lift_hold(self._pending)

    def note_remote_change(self) -> None:
        """New commits were pushed: the cached check is out of date, so check again now, in the background."""
        self._forget_check()
        self._last_attempt = 0.0
        self.schedule_refresh()

    def _ensure_waiter(self) -> None:
        loop = asyncio.get_running_loop()
        waiter = self._waiter
        if waiter is not None and not waiter.done() and waiter.get_loop() is loop and self._wake is not None:
            self._wake.set()
            return
        self._wake = asyncio.Event()   # one per waiter: an Event belongs to the loop it is first awaited on
        self._waiter = loop.create_task(self._wait_and_install())

    async def _nap(self, seconds: float) -> None:
        """Sleep until the next check, or until a new request wants a look now."""
        wake = self._wake
        if wake is None:
            await asyncio.sleep(seconds)
            return
        try:
            await asyncio.wait_for(wake.wait(), timeout=max(0.0, seconds))
        except asyncio.TimeoutError:
            pass
        wake.clear()

    async def _wait_and_install(self) -> None:
        """Every IDLE_CHECK_SECONDS: install when nothing is in progress, else take or lift the hold as the
        timings say. Ends once the update is installed (the process restarts), refused, cancelled or not needed."""
        try:
            while self._pending is not None:
                p = self._pending
                now = time.time()
                if p["automatic"] and storage.load_global_config().get("auto_update_enabled") is False:
                    self._end_pending("cancelled", "Automatic updates were switched off while the update waited.")
                    return
                if now < p["next_try"]:
                    await self._nap(min(IDLE_CHECK_SECONDS, p["next_try"] - now))
                    continue
                busy = await asyncio.to_thread(self.busy_reasons)   # the runner's lock is never waited on here
                if busy:
                    p["busy"] = busy
                    self._pace_hold(p, time.time())
                    await self._nap(IDLE_CHECK_SECONDS)
                    continue
                p["busy"] = []
                if await self._install_pending(p):
                    return
        except asyncio.CancelledError:
            if self._pending is not None and self._pending["state"] != "restarting":
                self._lift_hold(self._pending)
            raise
        except Exception as e:
            logger.error(f"The waiting update stopped on an unexpected error: {e}", exc_info=True)
            self._end_pending("error", f"Stopped on an unexpected error: {e}")

    async def _install_pending(self, p: Dict[str, Any]) -> bool:
        """Nothing is in progress: install. True when the waiting is over (restarting, or ended)."""
        p["state"] = "installing"
        status = await self.check_for_updates(force=True)
        if status.get("checked") and not status.get("update_available"):
            self._end_pending("up_to_date", "Already up to date: there was nothing to install.")
            return True
        result = await self.apply_update(force=False)
        outcome = result.get("status") or ""
        if result.get("success"):
            if outcome == "restarting":
                p["state"] = "restarting"   # the process exits in a moment; the hold stays until then
            else:
                self._end_pending(outcome or "installed", result.get("message") or "")
            return True
        if outcome in ("busy", "already_updating"):   # something started after the idle check
            p["state"] = "holding" if p["holding_since"] else "waiting"
            p["busy"] = result.get("busy") or self.busy_reasons() or ["work in progress"]
            await asyncio.sleep(IDLE_CHECK_SECONDS)   # not cut short: another install may be running
            return False
        p["attempts"] += 1
        p["last_result"] = {"status": outcome, "message": result.get("message") or ""}
        if outcome in RETRYABLE_FAILURES and p["attempts"] < MAX_INSTALL_ATTEMPTS:
            p["state"] = "retrying"
            p["next_try"] = time.time() + RETRY_SECONDS
            self._lift_hold(p)   # nothing is held through the pause; it is taken again if work is in progress then
            logger.warning(f"Installing the waiting update failed ({outcome}: {result.get('message')}); trying again "
                           f"in {_span(RETRY_SECONDS)} (attempt {p['attempts']} of {MAX_INSTALL_ATTEMPTS}).")
            return False
        self._end_pending(outcome or "failed", result.get("message") or "The update could not be installed.")
        return True

    def _pace_hold(self, p: Dict[str, Any], now: float) -> None:
        """Work is in progress: hold new work once the update has waited long enough (at once when a person
        asked), and lift a hold that has lasted HOLD_MAX_SECONDS without the work in hand ending."""
        if p["hold_token"] is None:
            if not p["hold_lifted"] and (p["hold_now"] or now - p["requested_at"] >= HOLD_AFTER_SECONDS):
                p["hold_token"] = work_hold.hold(HOLD_MESSAGE)
                p["holding_since"] = now
                logger.info(f"The waiting update holds new work until the work in hand is done "
                            f"({self._summary(p['busy'])}).")
        elif now - p["holding_since"] >= HOLD_MAX_SECONDS:
            self._lift_hold(p)
            p["hold_lifted"] = True
            logger.warning(f"New work was held for {_span(HOLD_MAX_SECONDS)} for the waiting update and work is "
                           f"still in progress ({self._summary(p['busy'])}). New work starts again; the update goes in "
                           f"at the first moment nothing is in progress.")
        p["state"] = "holding" if p["hold_token"] is not None else "waiting"

    @staticmethod
    def _lift_hold(p: Dict[str, Any]) -> None:
        token, p["hold_token"], p["holding_since"] = p["hold_token"], None, None
        if token is not None:
            work_hold.release(token)

    def _end_pending(self, outcome: str, message: str) -> None:
        p, self._pending = self._pending, None
        if p is None:
            return
        self._lift_hold(p)
        self._last_deferred = {"status": outcome, "message": message, "source": p["source"],
                               "requested_at": _iso(p["requested_at"]), "ended_at": _iso(time.time())}
        log = logger.info if outcome in ("up_to_date", "cancelled") else logger.warning
        log(f"The waiting update ({p['source']}) ended without a restart: {message}")

    def _describe(self, p: Dict[str, Any]) -> str:
        """One sentence that promises only what will happen."""
        state = p["state"]
        if state in ("installing", "restarting"):
            return "Installing the update now; OmbiSub restarts on the new version when it is in."
        if state == "retrying":
            failed = ((p["last_result"] or {}).get("message") or "").strip()
            return (f"The last attempt to install failed{f' ({failed[:160]})' if failed else ''}; "
                    f"OmbiSub tries again at {_clock(p['next_try'])}.")
        busy = p["busy"]
        if not busy:
            return "Nothing is in progress: installing the update now; OmbiSub restarts on the new version when it is in."
        what = self._summary(busy)
        if p["holding_since"] is not None:
            return (f"Waiting for the work in hand to finish ({what}). No new work starts until the update is "
                    "installed and OmbiSub has restarted.")
        if p["hold_lifted"]:
            return (f"Waiting for a moment with nothing in progress ({what}). New work is no longer held: it was "
                    f"held for {_span(HOLD_MAX_SECONDS)} without the work in hand finishing.")
        hold_at = p["requested_at"] + (0 if p["hold_now"] else HOLD_AFTER_SECONDS)
        return (f"Waiting for a moment with nothing in progress ({what}). If OmbiSub is still busy at "
                f"{_clock(hold_at)}, new work is held so that the work in hand can finish and the update go in.")

    async def apply_update(self, force: bool = False, target_tag: Optional[str] = None) -> Dict[str, Any]:
        """Safely pull release tag/commits, update dependencies, rebuild static, and restart.

        New work is held (services/work_hold.py) from the busy check to the restart: an episode that started
        while the web UI is rebuilt would be cut off by the restart. The hold is taken before the check, so
        no lane can claim between the two. An update that is refused or fails lifts it again."""
        if self._updating:
            return {"success": False, "status": "already_updating", "message": "Update is already in progress."}
        self._updating = True   # from here, not from the reset: two installs at once would both reset
        token = work_hold.hold(INSTALL_MESSAGE)
        restarting = False
        try:
            result = await self._install(force, target_tag)
            restarting = bool(result.get("success")) and result.get("status") == "restarting"
            return result
        finally:
            if not restarting:
                self._updating = False
                work_hold.release(token)

    async def _install(self, force: bool, target_tag: Optional[str]) -> Dict[str, Any]:
        # Guard: In-flight job check
        if not force and self.has_active_jobs():
            busy = self.busy_reasons()
            return {
                "success": False,
                "status": "busy",
                "busy": busy,
                "message": (f"Work is in progress ({self._summary(busy)}); installing now would cut it off."
                            if busy else "Work is in progress; installing now would cut it off."),
            }

        if not self.is_git_repo():
            return {
                "success": False,
                "status": "not_git",
                "message": "OmbiSub directory is not a Git repository. Automatic updates require a Git clone.",
            }

        # Guard: Working tree changes check. Only tracked files count: a reset --hard leaves untracked
        # files alone, and the data folder (update_check.json, logs, databases) lives inside the checkout.
        status_res = await asyncio.to_thread(self._run_git, ["status", "--porcelain", "--untracked-files=no"], 15.0)
        if status_res.returncode == 0 and status_res.stdout.strip():
            changed = len(status_res.stdout.strip().splitlines())
            logger.warning(f"Update not applied: {changed} file(s) have local changes a reset would erase.")
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
                "message": f"{changed} file(s) have uncommitted changes that an update would erase. Commit or stash them, then update.",
            }

        self._updating = True
        try:
            channel = CHANNEL_RELEASES if target_tag else self.resolve_channel()

            # Determine target ref: target_tag -> latest official release tag (releases channel) -> origin/<branch>
            tag_name = target_tag
            latest_rel = None
            if not tag_name and channel == CHANNEL_RELEASES:
                latest_rel = await asyncio.to_thread(self.fetch_latest_github_release)
                if latest_rel and latest_rel.get("tag_name"):
                    tag_name = latest_rel["tag_name"]

            if tag_name:
                logger.info(f"Applying update for release tag: {tag_name}")
                fetch_tags_res = await asyncio.to_thread(
                    self._run_git, ["fetch", "--tags", "origin", "--force"], 30.0
                )
                if fetch_tags_res.returncode != 0:
                    logger.warning(f"fetch --tags returned {fetch_tags_res.returncode}: {fetch_tags_res.stderr.strip()}")
                target_ref = f"tags/{tag_name}"

                # The reset below would drop commits that are not part of the release
                # (a development checkout ahead of it).
                if not force:
                    ahead_res = await asyncio.to_thread(
                        self._run_git, ["rev-list", "--count", f"{target_ref}..HEAD"], 10.0
                    )
                    ahead = ahead_res.stdout.strip() if ahead_res.returncode == 0 else ""
                    if ahead.isdigit() and int(ahead) > 0:
                        self._updating = False
                        logger.warning(f"Update not applied: {ahead} local commit(s) are not part of {tag_name}.")
                        return {
                            "success": False,
                            "status": "local_commits",
                            "message": (
                                f"{ahead} local commit(s) are not part of release {tag_name}; installing it "
                                "would discard them. Push them and publish a newer release first."
                            ),
                        }
            else:
                current_branch = self.get_current_version_info().get("branch") or DEFAULT_BRANCH
                branch = DEFAULT_BRANCH if channel == CHANNEL_MAIN else current_branch
                if channel == CHANNEL_MAIN and current_branch not in (DEFAULT_BRANCH, "HEAD"):
                    # The reset below would move whatever branch is checked out onto origin/main.
                    self._updating = False
                    return {
                        "success": False,
                        "status": "wrong_branch",
                        "message": f"The checkout is on branch '{current_branch}', not '{DEFAULT_BRANCH}'; "
                                   f"the {CHANNEL_MAIN} channel only updates the {DEFAULT_BRANCH} branch.",
                    }
                logger.info(f"Applying update: pulling from origin/{branch}...")
                fetch_res = await asyncio.to_thread(self._run_git, ["fetch", "origin", branch], 30.0)
                if fetch_res.returncode != 0:
                    self._updating = False
                    return {
                        "success": False,
                        "status": "fetch_failed",
                        "message": f"git fetch failed: {fetch_res.stderr.strip()}",
                    }

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
                target_ref = f"origin/{branch}"

            # Check modified files between HEAD and target ref
            diff_res = await asyncio.to_thread(
                self._run_git, ["diff", "--name-only", "HEAD", target_ref], 15.0
            )
            changed_files = diff_res.stdout.strip().splitlines() if diff_res.returncode == 0 else []
            reqs_changed = any(f.startswith(("backend/requirements", "requirements")) for f in changed_files)
            frontend_changed = any(f.startswith("frontend/") for f in changed_files)

            # Pre-reset safety check
            if not force:
                pre_reset_status = await asyncio.to_thread(self._run_git, ["status", "--porcelain", "--untracked-files=no"], 15.0)
                if pre_reset_status.returncode == 0 and pre_reset_status.stdout.strip():
                    self._updating = False
                    return {
                        "success": False,
                        "status": "skipped",
                        "reason": "dirty_working_tree",
                        "message": "Working tree has uncommitted changes; commit or stash before updating.",
                    }

            # Hard reset to target ref
            reset_res = await asyncio.to_thread(self._run_git, ["reset", "--hard", target_ref], 30.0)
            if reset_res.returncode != 0:
                self._updating = False
                return {
                    "success": False,
                    "status": "reset_failed",
                    "message": f"git reset failed: {reset_res.stderr.strip()}",
                }

            # Install updated requirements if changed
            if reqs_changed:
                logger.info("backend/requirements changed; running pip install...")
                await asyncio.to_thread(self._pip_install)

            # Rebuild frontend static assets if changed (or if there is no build at all)
            if frontend_changed or not (self.static_dir / "index.html").is_file():
                logger.info("frontend/ changed (or no UI build exists); refreshing static assets...")
                static_downloaded = False
                if latest_rel and latest_rel.get("assets"):
                    static_asset = next(
                        (a for a in latest_rel["assets"] if a.get("name") == "static.zip"),
                        None
                    )
                    if static_asset and static_asset.get("browser_download_url"):
                        try:
                            import zipfile
                            req = Request(static_asset["browser_download_url"], headers={"User-Agent": "OmbiSub-Updater"})
                            def _extract_zip():
                                with urlopen(req, timeout=30.0) as resp:
                                    zf = zipfile.ZipFile(io.BytesIO(resp.read()))
                                    dest = self.static_dir
                                    dest.mkdir(parents=True, exist_ok=True)
                                    zf.extractall(dest)
                            await asyncio.to_thread(_extract_zip)
                            static_downloaded = True
                            logger.info("Successfully extracted release static.zip to backend/static.")
                        except Exception as e:
                            logger.warning(f"Failed extracting static.zip from release asset: {e}")

                if not static_downloaded:
                    await asyncio.to_thread(self._build_frontend)

            # Write release_version.json
            new_version = self.get_current_version_info()
            version_data = {
                "tag_name": tag_name or new_version.get("commit_short", "latest"),
                "commit_sha": new_version.get("commit_hash", ""),
                "channel": CHANNEL_RELEASES if tag_name else channel,
                "installed_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                self.RELEASE_VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
                self.RELEASE_VERSION_FILE.write_text(json.dumps(version_data, indent=2), encoding="utf-8")
                logger.info(f"Recorded installed release to {self.RELEASE_VERSION_FILE}: {version_data['tag_name']}")
            except Exception as e:
                logger.warning(f"Failed writing release_version.json: {e}")

            # Invalidate cached status
            self._forget_check()

            # Trigger graceful restart
            logger.info("Update applied successfully. Triggering server restart...")
            if self.trigger_restart(delay_seconds=1.5) is False:
                self._updating = False
                return {
                    "success": True,
                    "status": "installed",
                    "message": f"Update installed ({tag_name or target_ref}), but OmbiSub could not restart itself. "
                               "Restart it to run the new version.",
                }

            return {
                "success": True,
                "status": "restarting",
                "message": f"Update installed successfully ({tag_name or target_ref}). OmbiSub is now restarting...",
            }
        except Exception as e:
            self._updating = False
            logger.error(f"Failed to apply update: {e}", exc_info=True)
            return {"success": False, "status": "error", "message": str(e)}

    def _pip_install(self) -> None:
        """Install the locked requirements (the plain list when there is no lock file) into this Python."""
        req = "requirements.lock.txt" if (self.backend_dir / "requirements.lock.txt").is_file() else "requirements.txt"
        try:
            res = subprocess.run(
                [str(PYTHON_EXE), "-m", "pip", "install", "-r", req],
                cwd=str(self.backend_dir),
                capture_output=True,
                text=True,
                timeout=600.0,
                check=False,
                creationflags=CREATE_NO_WINDOW,
            )
            if res.returncode != 0:
                logger.warning(f"pip install -r {req} failed ({res.returncode}): {str(res.stderr)[-500:]}")
        except Exception as e:
            logger.warning(f"pip install completed with warning: {e}")

    def _build_frontend(self) -> bool:
        """``npm ci && npm run build`` in frontend/, then swap frontend/dist into backend/static.

        The previous build stays in place until the new one is complete; without npm (or when the build
        fails) it is left untouched. Returns whether backend/static was replaced.
        """
        npm = shutil.which("npm")
        if not npm:
            logger.warning("npm was not found: the web UI was not rebuilt (backend/static is unchanged).")
            return False
        try:
            for cmd in (["npm", "ci"], ["npm", "run", "build"]):
                logger.info(f"Frontend: {' '.join(cmd)}")
                res = subprocess.run(
                    cmd,
                    cwd=str(self.frontend_dir),
                    shell=(sys.platform == "win32"),  # npm is npm.cmd on Windows
                    capture_output=True,
                    text=True,
                    timeout=600.0,
                    check=False,
                    creationflags=CREATE_NO_WINDOW,
                )
                if res.returncode != 0:
                    logger.warning(f"{' '.join(cmd)} failed ({res.returncode}): {str(res.stderr)[-500:]}")
                    return False
            dist = self.frontend_dir / "dist"
            if not (dist / "index.html").is_file():
                logger.warning("The frontend build produced no dist/index.html; backend/static is unchanged.")
                return False
            self._swap_static(dist)
            logger.info("Web UI rebuilt and installed in backend/static.")
            return True
        except Exception as e:
            logger.warning(f"Frontend build completed with warning: {e}")
            return False

    def _swap_static(self, dist: Path) -> None:
        """Replace the static dir with a copy of ``dist``; the old one is only removed once the new is in place."""
        static = self.static_dir
        new, old = static.with_name(static.name + ".new"), static.with_name(static.name + ".old")
        for leftover in (new, old):
            shutil.rmtree(leftover, ignore_errors=True)
        shutil.copytree(dist, new)
        try:
            if static.exists():
                static.rename(old)
            new.rename(static)
        except OSError:
            if old.exists() and not static.exists():
                old.rename(static)  # put the previous build back
            raise
        shutil.rmtree(old, ignore_errors=True)

    def trigger_restart(self, delay_seconds: float = 1.0) -> bool:
        """Spawn detached restart helper script or exit with code 42 under tray supervisor. True when the exit
        is scheduled, False when nothing will restart the server."""
        if os.environ.get("OMBISUB_SUPERVISOR") == "tray":
            logger.info("Running under OMBISUB_SUPERVISOR=tray; exiting with code 42 for automatic tray respawn.")
            threading.Timer(0.5, lambda: os._exit(42)).start()
            return True

        helper_script = ROOT_DIR / "scripts" / "restart_helper.py"
        if not helper_script.is_file():
            logger.error(f"Restart helper script not found at {helper_script}")
            return False

        parent_pid = os.getpid()
        cmd = [
            str(PYTHON_EXE),
            str(helper_script),
            "--pid", str(parent_pid),
            "--backend-dir", str(BACKEND_DIR),
            "--python-exe", str(PYTHON_EXE),
            "--port", os.environ.get("OMBISUB_PORT") or "8000",
            "--service-name", "OmbiSub",
        ]

        flags = CREATE_NO_WINDOW
        if sys.platform == "win32":
            # DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP ensures restarter survives parent shutdown
            flags |= subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008  # DETACHED_PROCESS

        try:
            subprocess.Popen(
                cmd,
                cwd=str(ROOT_DIR),
                creationflags=flags,
                close_fds=True,
            )
            logger.info(f"Detached restart helper launched for PID {parent_pid}.")
            threading.Timer(0.5, lambda: os._exit(0)).start()
            return True
        except Exception as e:
            logger.error(f"Failed to launch restart helper: {e}")
            return False


# Singleton instance
update_service = UpdateService()
