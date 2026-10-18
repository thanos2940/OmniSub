"""
A stand-in for Antigravity's ``agy`` CLI, for the direct-runner tests (no network, no quota).

It answers the runner's prompts the way a model would: every ``N| English`` line in the
"Lines to translate" / "Lines to fix" section comes back as ``N| Ελ: English`` (Greek script,
so it counts as translated, and still recognisably the translation of its own line), plus
``S|``/``G|`` lines and a new term when the prompt asks for them. ``agy -p /quota`` prints a
quota report. Behaviour switches (environment variables):

  FAKE_AGY_LOG            folder: each prompt is saved there (call_001.txt, ...)
  FAKE_AGY_SKIP_AT        in translate requests, drop the answer to this line but keep counting
                          (every later answer lands one number too early, the last is missing)
  FAKE_AGY_QUOTA_MODELS   comma-separated model prefixes that fail with a quota error (and
                          show up as used up in the quota report)
  FAKE_AGY_BLOCK_MODELS   comma-separated model prefixes whose safety filter refuses any
                          request containing FAKE_AGY_BLOCK_WORD
  FAKE_AGY_CUT            with the above: an output filter, which streams the answers before the
                          first line with the word and then stops the reply
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

LINE = re.compile(r"^(\d+)\| (.*)$")


def arg(name, default=""):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def matches(model, env):
    prefixes = [p.strip() for p in os.environ.get(env, "").split(",") if p.strip()]
    return any(model.startswith(p) for p in prefixes)


def quota_report():
    soon = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    gemini_out = matches("gemini", "FAKE_AGY_QUOTA_MODELS")
    groups = [
        {"name": "Gemini Models", "description": "Gemini models", "buckets": [
            {"window": "5h", "remaining_fraction": 0.0 if gemini_out else 1.0, "reset_time": soon}]},
        {"name": "Claude and GPT models", "description": "Claude and GPT models", "buckets": [
            {"window": "5h", "remaining_fraction": 1.0, "reset_time": soon}]},
    ]
    print(json.dumps({"command": {"data": {"groups": groups}}}))


def lines_to_answer(prompt):
    """(id, escaped text) pairs of the section the model must answer."""
    out, inside = [], False
    for line in prompt.splitlines():
        if line.startswith("## "):
            inside = line.startswith("## Lines to translate") or line.startswith("## Lines to fix")
            continue
        if inside:
            m = LINE.match(line)
            if m:
                out.append((int(m.group(1)), m.group(2)))
    return out


def fail(message):
    print(json.dumps({"event": "result", "result": {"status": "ERROR", "error": message}}))
    sys.exit(1)


def main():
    sys.stdin.reconfigure(encoding="utf-8")   # the runner talks UTF-8, whatever the console code page
    sys.stdout.reconfigure(encoding="utf-8")
    if "-p" in sys.argv and arg("-p") == "/quota":
        quota_report()
        return
    request = json.loads(sys.stdin.readline())
    prompt = request["message"]["content"]
    model = arg("--model")
    log = os.environ.get("FAKE_AGY_LOG")
    if log:
        folder = Path(log)
        folder.mkdir(parents=True, exist_ok=True)
        n = len(list(folder.glob("call_*.txt"))) + 1
        (folder / f"call_{n:03d}.txt").write_text(f"model: {model}\n\n{prompt}", encoding="utf-8")

    if matches(model, "FAKE_AGY_QUOTA_MODELS"):
        fail("RESOURCE_EXHAUSTED: You have exhausted your quota on this model. Your quota will reset after 1h0m0s.")
    pairs = lines_to_answer(prompt)
    word = os.environ.get("FAKE_AGY_BLOCK_WORD", "")
    if word and matches(model, "FAKE_AGY_BLOCK_MODELS") and any(word in text for _, text in pairs):
        if os.environ.get("FAKE_AGY_CUT"):
            before = []
            for n, t in pairs:
                if word in t:
                    break
                before.append(f"{n}| Ελ: {t}")
            print(json.dumps({"event": "step_update", "step_update": {"step_type": "agent_response",
                                                                      "text_delta": "\n".join(before)}}))
        fail("The response was blocked by the content safety filter (HARM_CATEGORY_SEXUAL).")

    ids = [n for n, _ in pairs]
    texts = [t for _, t in pairs]
    skip = os.environ.get("FAKE_AGY_SKIP_AT")
    if skip and "## Lines to translate" in prompt and int(skip) in ids:
        k = ids.index(int(skip))
        texts = texts[:k] + texts[k + 1:]   # one answer missing, numbering carries on
        ids = ids[:-1]
    answer = [f"{n}| Ελ: {t}" for n, t in zip(ids, texts)]
    if "starting with `S| `" in prompt:
        answer += ["S| Ο Τζόνι περιμένει κάποιον στο σταθμό.", "S| Στο τέλος φεύγουν μαζί."]
    if "starting with `G| `" in prompt:
        answer += ["G| Μια ήσυχη δραματική σειρά για δύο φίλους."]
    if "+ term | translation" in prompt:
        answer += ["+ Johnny | Τζόνι | character | masculine"]
    text = "\n".join(answer)
    time.sleep(0.05)
    print(json.dumps({"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": text}}))
    print(json.dumps({"event": "result", "result": {"status": "SUCCESS", "response": text,
                                                    "usage": {"input_tokens": len(prompt) // 4,
                                                              "output_tokens": len(text) // 4}}}))


if __name__ == "__main__":
    main()
