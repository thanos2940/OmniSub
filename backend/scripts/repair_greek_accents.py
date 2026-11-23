"""
Find (and optionally undo) accents the old Japanese-name guess moved in Greek subtitles (utils/accent_repair.py).

It only changes a word when the same project shows its real accent elsewhere, in lower case inside a longer
line, and lists the capitalised words it cannot decide (names, mostly) for a person to check. Nothing is
written without --apply; --export also rewrites the subtitle files next to the videos.

Run from backend/ with Omnisub stopped (or at least idle):
    ../.venv/Scripts/python.exe -m scripts.repair_greek_accents                   # report only
    ../.venv/Scripts/python.exe -m scripts.repair_greek_accents --apply --export  # fix and re-export
    ... --project "Breaking Bad"                                                  # one project
Set OMNISUB_DATA_DIR first when the data folder is not backend/.
"""
import argparse
from collections import Counter
from typing import Dict, List

from utils import storage
from utils.accent_repair import collect_evidence, repair_line
from utils.language_codes import to_code


def _texts(cue: Dict, code: str, primary: bool) -> List[str]:
    out = [(cue.get("translations") or {}).get(code) or ""]
    if primary:
        out.append(cue.get("translated") or "")
    return [t for t in out if t]


def repair_project(project: str, apply: bool, export: bool) -> Dict:
    meta = storage.load_project_metadata(project) or {}
    languages = storage.project_target_languages(meta)
    greek = [lang for lang in languages if to_code(lang) == "el"]
    if not greek:
        return {"skipped": True}
    code, primary = "el", to_code(meta.get("target_language") or "") == "el"
    episodes = storage.list_episodes(project) or []
    loaded = {ep: storage.load_episode_data(project, ep) or [] for ep in episodes}
    evidence = collect_evidence(t for cues in loaded.values() for cue in cues for t in _texts(cue, code, primary))

    changed_eps, examples, unresolved = [], Counter(), Counter()
    for ep, cues in loaded.items():
        touched = False
        for cue in cues:
            seen = set()   # the same text sits in "translations" and "translated": count it once
            for field in ("translations", "translated"):
                text = (cue.get("translations") or {}).get(code) if field == "translations" else (
                    cue.get("translated") if primary else None)
                if not text:
                    continue
                new, changes, left = repair_line(text, evidence)
                if text not in seen:
                    seen.add(text)
                    unresolved.update(left)
                    examples.update(f"{a} → {b}" for a, b in changes)
                if new != text:
                    if field == "translations":
                        cue["translations"][code] = new
                    else:
                        cue["translated"] = new
                    touched = True
        if touched:
            changed_eps.append(ep)
            if apply:
                storage.save_episode(project, ep, cues)
                if export:
                    from services.agent_task_service import AgentTaskService
                    AgentTaskService._export(project, ep, cues)
    return {"episodes": changed_eps, "examples": examples, "unresolved": unresolved}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the corrected episodes")
    ap.add_argument("--export", action="store_true", help="with --apply: re-export the corrected episodes")
    ap.add_argument("--project", help="only this project")
    args = ap.parse_args()
    projects = [args.project] if args.project else storage.list_projects()
    total = 0
    for project in projects:
        res = repair_project(project, args.apply, args.apply and args.export)
        if res.get("skipped") or not (res["episodes"] or res["unresolved"]):
            continue
        total += len(res["episodes"])
        print(f"\n== {project}: {len(res['episodes'])} episode(s) {'fixed' if args.apply else 'to fix'}")
        for change, n in res["examples"].most_common(15):
            print(f"   {n:4d} × {change}")
        names = [f"{w} ({n})" for w, n in res["unresolved"].most_common(12) if n >= 2]
        if names:
            print("   check by hand (no lower-case evidence, often names): " + ", ".join(names))
    print(f"\n{total} episode(s) {'fixed' if args.apply else 'would be fixed (run again with --apply)'}.")
    if args.apply and not args.export:
        print("The subtitle files next to the videos still have the old text: re-export, or run with --export.")


if __name__ == "__main__":
    main()
