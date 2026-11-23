"""The models a runner may use: which one to pick, quota pauses, and what a failed request means.

Part of the agent runner (services/agent_runner.py): ``AgentRunner`` inherits this mixin. Moved here unchanged
from that module; ``self`` is the runner.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from services import agent_cli
from services.runner.common import QUOTA_MARGIN, QUOTA_CACHE_SECONDS, _human


class ModelsMixin:
    def _exe(self) -> Optional[List[str]]:
        engine = self.config["engine"]
        return self.exe_override.get(engine) or agent_cli.engine_command(engine)

    def _models(self) -> List[str]:
        out = [self.config["model"] or ""]
        for m in self.config["fallback_models"] or []:
            m = (m or "").strip()
            if m and m not in out:
                out.append(m)
        return out

    def _available(self, model: str) -> bool:
        with self.lock:
            return self.pauses.get(model or "default", 0) <= time.time()

    def _pick(self, lane: Optional[int] = None) -> Tuple[Optional[str], float]:
        """The first usable model. With ``lane`` (about to start an episode): the provider's quota is
        checked first, and after a pause only one lane at a time tests whether a model is back."""
        if lane is not None:
            self._check_quota()
        now = time.time()
        with self.lock:
            waits = []
            for m in self._models():
                key = m or "default"
                until = self.pauses.get(key, 0)
                if until > now:
                    waits.append(until - now)
                    continue
                if key in self.recovering:
                    owner = self.recovering[key]
                    if lane is not None and owner not in (None, lane):
                        waits.append(10.0)
                        continue
                    if lane is not None:
                        self.recovering[key] = lane
                return m, 0.0
        return None, min(waits) if waits else 60.0

    def _release_probe(self, model: Optional[str], lane: int) -> None:
        with self.lock:
            key = model or "default"
            if self.recovering.get(key) == lane:
                self.recovering[key] = None

    def _pause(self, model: str, seconds: float, why: str, group: Optional[List[str]] = None) -> None:
        until_ts = time.time() + seconds
        keys = sorted({(m or "default") for m in (group or [model])})
        with self.lock:
            fresh = [k for k in keys if self.pauses.get(k, 0) < until_ts - 60]
            for k in keys:
                self.pauses[k] = max(self.pauses.get(k, 0), until_ts)
                self.recovering[k] = None
        if not fresh:
            return
        self._save()
        until = datetime.now() + timedelta(seconds=seconds)
        self.log(f"{why} — {', '.join(keys)} paused until {until:%H:%M} ({_human(seconds)}).", "warn")

    def _recovered(self, model: str) -> None:
        with self.lock:
            self.recovering.pop(model or "default", None)
            self.quota_streak[model or "default"] = 0

    def quota_status(self, force: bool = False) -> Optional[Dict]:
        if self.config["engine"] != "antigravity" or self.quota_unsupported:
            return None
        with self.quota_lock:
            fresh = self.quota and time.time() - self.quota["fetched"] < QUOTA_CACHE_SECONDS
            if force or not fresh:
                exe = self._exe()
                groups = agent_cli.antigravity_quota(exe) if exe else None
                if groups == []:
                    self.quota_unsupported = True
                    self.log("Antigravity has no /quota report in this version; quota is tracked from errors only.", "warn")
                self.quota = {"groups": groups or [], "fetched": time.time()}  # failures are cached too
            return self.quota if self.quota and self.quota["groups"] else None

    def _quota_group(self, model: str) -> Tuple[Optional[Dict], List[str]]:
        q = self.quota_status()
        if not q:
            return None, [model]
        group = agent_cli.group_of(model, q["groups"])
        if not group:
            return None, [model]
        members = [m for m in self._models() if agent_cli.group_of(m, q["groups"]) is group]
        return group, sorted(members) or [model]

    def _check_quota(self) -> None:
        """Pause models whose quota group the provider reports as used up (no request is spent)."""
        for m in self._models():
            if not self._available(m):
                continue
            group, members = self._quota_group(m)
            until = agent_cli.exhausted_until(group) if group else None
            if until and until > time.time():
                self._pause(m, until - time.time() + QUOTA_MARGIN,
                            f"{group['name']}: quota used up (reported by Antigravity)", group=members)

    def _failure(self, reply: agent_cli.Reply, model: str, lane: int, what: str) -> None:
        kind = reply.failure
        if kind == "stopped":
            return
        key = model or "default"
        if kind == "auth":
            self._finish("error", f"{agent_cli.ENGINES[self.config['engine']]} is not signed in or not allowed: "
                                  f"{reply.message[:200]} — sign in (run the CLI once in a terminal), then start again.",
                         "error")
            return
        if kind == "quota":
            with self.lock:
                if self.pauses.get(key, 0) > time.time():
                    return  # another lane already reported this quota stop
                self.session["quota_hits"] += 1
                streak = self.quota_streak.get(key, 0) + 1
                self.quota_streak[key] = streak
            members = [model]
            if self.config["engine"] == "antigravity":  # ask when it refills, for the whole quota group
                self.quota_status(force=True)
                group, members = self._quota_group(model)
                until = (agent_cli.exhausted_until(group) or agent_cli.exhausted_until(group, 0.10)) if group else None
                if until and until > time.time():
                    self._pause(model, until - time.time() + QUOTA_MARGIN,
                                f"Quota reached — {group['name']} refills at the time Antigravity reports", group=members)
                    return
            if reply.wait is not None:
                wait, how = reply.wait + QUOTA_MARGIN, "reset time from the error"
            else:
                wait, how = min(3600, 600 * 2 ** (streak - 1)), "no reset time given; backing off"
            self._pause(model, wait, f"Quota reached ({how}): {reply.message[:160]}", group=members)
            return
        if kind == "rate":
            self._pause(model, reply.wait + 5 if reply.wait is not None else 90, f"Rate limited: {reply.message[:160]}")
            return
        with self.lock:
            self.consecutive_errors += 1
            errors = self.consecutive_errors
        self.log(f"Lane {lane + 1}: {what} failed ({reply.message[:300]}).", "error")
        if errors >= 8:
            self._finish("error", "Stopped after 8 failures in a row; see the log.", "error")
        elif errors >= 3:
            self._pause(model, 600, "Several failures in a row")
        else:
            self.stop_event.wait(20)
