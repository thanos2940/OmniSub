"""Human-readable report + baseline diff for eval runs (Plan 11)."""

import json
from typing import Dict, Optional


def format_report(metrics: Dict, baseline: Optional[Dict] = None) -> str:
    lines = ["=== Omnisub translation eval ===", f"items: {metrics.get('count', 0)}", ""]
    keys = ["chrf", "glossary_adherence", "format_integrity", "consistency", "overall"]
    for k in keys:
        v = metrics.get(k)
        cur = "n/a" if v is None else f"{v:.4f}"
        if baseline and baseline.get(k) is not None and v is not None:
            delta = v - baseline[k]
            arrow = "▲" if delta > 0.0005 else ("▼" if delta < -0.0005 else "=")
            lines.append(f"  {k:20s} {cur}   {arrow} {delta:+.4f} (baseline {baseline[k]:.4f})")
        else:
            lines.append(f"  {k:20s} {cur}")
    if baseline:
        reg = [k for k in keys if metrics.get(k) is not None and baseline.get(k) is not None and metrics[k] < baseline[k] - 0.01]
        lines.append("")
        lines.append("REGRESSIONS: " + (", ".join(reg) if reg else "none"))
    return "\n".join(lines)


def load_json(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
