"""
Adaptive concurrency controller (Plan 10).

Per-model AIMD (additive-increase / multiplicative-decrease) tuning of how many
episodes the worker runs in parallel, driven by observed rate-limit signals:
  - a window of successful calls with no backoff  -> +1 (additive increase)
  - a 429 / reported rate-limit                    -> halve (multiplicative decrease)

In-memory only; on restart each model resets to the configured base (safe).
"""

import threading
from typing import Dict


class ConcurrencyController:
    def __init__(self, model: str, base: int, c_max: int):
        self.model = model
        self.c_max = max(1, int(c_max))
        self._c = float(max(1, int(base)))
        self._success_streak = 0

    def current(self) -> int:
        return max(1, min(self.c_max, int(round(self._c))))

    def on_success(self) -> None:
        self._success_streak += 1
        if self._success_streak >= 5 and self._c < self.c_max:
            self._c = min(self.c_max, self._c + 1.0)
            self._success_streak = 0

    def on_rate_limited(self) -> None:
        self._c = max(1.0, self._c * 0.5)
        self._success_streak = 0


class ConcurrencyManager:
    def __init__(self):
        self._controllers: Dict[str, ConcurrencyController] = {}
        self._lock = threading.Lock()
        self.enabled = False
        self.base = 2
        self.c_max = 6

    def set_defaults(self, enabled: bool, base: int, c_max: int) -> None:
        self.enabled = bool(enabled)
        self.base = max(1, int(base))
        self.c_max = max(1, int(c_max))

    def _get(self, model: str) -> ConcurrencyController:
        with self._lock:
            c = self._controllers.get(model)
            if c is None:
                c = ConcurrencyController(model, self.base, self.c_max)
                self._controllers[model] = c
            else:
                c.c_max = self.c_max
            return c

    def current(self, model: str) -> int:
        if not self.enabled or model.startswith("local/"):
            return self.base
        return self._get(model).current()

    def on_success(self, model: str) -> None:
        if self.enabled and not model.startswith("local/"):
            self._get(model).on_success()

    def on_rate_limited(self, model: str) -> None:
        if self.enabled and not model.startswith("local/"):
            self._get(model).on_rate_limited()

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {m: c.current() for m, c in self._controllers.items()}


# Singleton
concurrency_manager = ConcurrencyManager()
