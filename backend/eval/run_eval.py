"""
Translation eval runner (Plan 11).

Usage:
  python -m eval.run_eval                          # score the example dataset (offline)
  python -m eval.run_eval --translate --model X    # translate with the real model, then score
  python -m eval.run_eval --candidates hyps.json   # score externally-produced hypotheses
  python -m eval.run_eval --baseline prev.json --out report.json

Exits non-zero if a key metric regresses vs --baseline (CI-friendly).
"""

import argparse
import asyncio
import json
from pathlib import Path

from eval import metrics as M
from eval.report import format_report, load_json

EVAL_DIR = Path(__file__).resolve().parent


async def translate_items(items, model: str, target_language: str):
    """Translate each item's source with the real prompt + parser (best-effort)."""
    from adk_agents.translator_agent import build_translation_prompt
    from utils.llm_utils import parse_translations_from_text, strip_reasoning_blocks
    from adk_agents.llm_factory import generate

    for it in items:
        src_lines = it["source"].split("\n")
        prompt = build_translation_prompt(src_lines, target_language)
        
        # Use the unified generate seam (handles both Cloud and Local)
        text = await generate(model_name=model, prompt=prompt)
        
        parsed = parse_translations_from_text(strip_reasoning_blocks(text))
        out = [""] * len(src_lines)
        for p in parsed:
            i = p["index"] - 1
            if 0 <= i < len(out):
                out[i] = p["text"]
        it["hypothesis"] = "\n".join(out)
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(EVAL_DIR / "dataset" / "example.json"))
    ap.add_argument("--translate", action="store_true", help="translate sources with the model")
    ap.add_argument("--model", default="gemini-flash-lite-latest")
    ap.add_argument("--target-language", default="Greek")
    ap.add_argument("--candidates", help="JSON list of hypothesis strings aligned to the dataset")
    ap.add_argument("--out", default=None, help="write metrics JSON here")
    ap.add_argument("--baseline", default=None, help="compare against a previous metrics JSON")
    args = ap.parse_args()

    items = load_json(args.dataset)
    if args.candidates:
        for it, h in zip(items, load_json(args.candidates)):
            it["hypothesis"] = h
    elif args.translate:
        asyncio.run(translate_items(items, args.model, args.target_language))

    metrics = M.score_all(items)
    baseline = load_json(args.baseline) if args.baseline else None
    print(format_report(metrics, baseline))

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(metrics, f, indent=2, ensure_ascii=False)

    if baseline:
        for k in ("overall", "chrf", "glossary_adherence"):
            cur, base = metrics.get(k), baseline.get(k)
            if cur is not None and base is not None and cur < base - 0.01:
                raise SystemExit(2)


if __name__ == "__main__":
    main()
