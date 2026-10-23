"""What a Gemini API model accepts, for the requests Omnisub builds.

Thinking (Gemini 2.5 and later) is billed as output, so mechanical passes ask for a budget of 0. Which
models take a thinking setting cannot be read reliably from a name: the ``-latest`` aliases
(``gemini-flash-latest``, ``gemini-flash-lite-latest``, ``gemini-pro-latest``) carry no version and move
to newer models over time, and some models only work in thinking mode. So:

* ``thinking_config(model, budget)`` sends the budget to every Gemini model except the generations that
  had no thinking (1.x, 2.0) and Gemma, never 0 to a Pro model, and nothing to a model the API has
  already refused it for;
* ``drop_rejected_thinking(model, config, exc)``: when the API answers 400 about the thinking setting,
  the model is remembered (for this process) and the setting is removed from the request, which the
  caller then sends once more. Anything else is left to the caller.
"""

import logging
import re
import threading
from typing import Any, Optional, Set

logger = logging.getLogger("omnisub.gemini_models")

_NO_THINKING_GENERATIONS = re.compile(r"^(models/)?(gemini-(1\.|2\.0)|gemma-)")

_lock = threading.Lock()
_refused: Set[str] = set()  # models whose API refused a thinking setting: send none from now on


def _key(model: Optional[str]) -> str:
    m = (model or "").strip().lower()
    return m[len("models/"):] if m.startswith("models/") else m


def supports_thinking(model: Optional[str]) -> bool:
    m = _key(model)
    if not m or _NO_THINKING_GENERATIONS.match(m):
        return False
    return m.startswith("gemini-") or "thinking" in m


def thinking_config(model: Optional[str], budget: Optional[int]):
    """The ThinkingConfig to send with a request, or None to leave the model's default."""
    if budget is None or budget < 0 or not supports_thinking(model):
        return None
    m = _key(model)
    with _lock:
        if m in _refused:
            return None
    if budget == 0 and "pro" in m:  # Pro models only work in thinking mode
        return None
    try:
        from google.genai import types
        return types.ThinkingConfig(thinking_budget=budget)
    except Exception:  # an SDK without thinking support: keep the model default
        return None


def refused(model: Optional[str]) -> bool:
    """True once the API has refused a thinking setting for this model."""
    with _lock:
        return _key(model) in _refused


def is_thinking_rejection(exc: BaseException) -> bool:
    """A 400 from the API about the thinking setting (e.g. "Budget 0 is invalid. This model only works in
    thinking mode." or "Thinking is not supported by this model.")."""
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    text = str(exc).lower()
    if code != 400 and "400" not in text[:40] and "invalid_argument" not in text:
        return False
    return "thinking" in text or "budget" in text


def drop_rejected_thinking(model: Optional[str], config: Any, exc: BaseException) -> bool:
    """If ``exc`` is the API refusing ``config``'s thinking setting: remember the model, clear the setting
    on ``config`` and return True (send the request again). Otherwise False."""
    if config is None or getattr(config, "thinking_config", None) is None or not is_thinking_rejection(exc):
        return False
    m = _key(model)
    with _lock:
        first = m not in _refused
        _refused.add(m)
    if first:
        logger.warning(f"Gemini model '{model}' refused the thinking setting ({str(exc)[:160]}); "
                       f"sending its requests without one.")
    try:
        config.thinking_config = None
    except Exception:
        return False
    return True


def forget_refusals() -> None:
    """For tests."""
    with _lock:
        _refused.clear()
