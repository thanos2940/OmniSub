"""
One-time script to sanitize existing affected episodes in Solo Leveling
and update on-disk sidecars and LanceDB Translation Memory.
"""

import os
import sys
import json
import glob
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from utils.text_normalize import sanitize_translation_text
from utils.source_clean import reconstruct_cleaned_srt
from utils import storage

def repair_solo_leveling():
    project_name = "Solo Leveling"
    proj_dir = backend_dir / "projects" / project_name
    episodes_dir = proj_dir / "episodes"
    
    total_cleaned_cues = 0
    total_episodes_affected = 0

    episodes = sorted([d for d in episodes_dir.iterdir() if d.is_dir()])
    print(f"Scanning {len(episodes)} episodes in {project_name}...")

    for ep_dir in episodes:
        ep_name = ep_dir.name
        data_path = ep_dir / "data.json"
        meta_path = ep_dir / "metadata.json"

        if not data_path.exists():
            continue

        with open(data_path, "r", encoding="utf-8") as f:
            ep_data = json.load(f)

        items = ep_data.get("data", []) if isinstance(ep_data, dict) else ep_data
        ep_cleaned = 0

        for item in items:
            orig = item.get("original", "")
            trans = item.get("translated", "")
            cleaned_trans = sanitize_translation_text(trans, source_text=orig, target_lang="Greek", target_lang_code="el")

            changed = False
            if cleaned_trans != trans:
                item["translated"] = cleaned_trans
                changed = True
                ep_cleaned += 1

            translations = item.get("translations") or {}
            for lang_code, t_val in list(translations.items()):
                clean_val = sanitize_translation_text(t_val, source_text=orig, target_lang_code=lang_code)
                if clean_val != t_val:
                    translations[lang_code] = clean_val
                    changed = True

        if ep_cleaned > 0:
            total_cleaned_cues += ep_cleaned
            total_episodes_affected += 1
            print(f"[{ep_name}] Sanitized {ep_cleaned} contaminated cues in data.json.")

            # Save data.json
            with open(data_path, "w", encoding="utf-8") as f:
                json.dump(ep_data, f, indent=2, ensure_ascii=False)

            # Reconstruct and re-export sidecar to arr_target_path
            meta = {}
            if meta_path.exists():
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)

            target_path = meta.get("arr_target_path")
            if target_path and os.path.exists(target_path):
                print(f"  Updating on-disk sidecar: {target_path}")
                reconstructed = reconstruct_cleaned_srt(project_name, ep_name, items, target_lang_code="el")
                try:
                    with open(target_path, "w", encoding="utf-8-sig") as sf:
                        sf.write(reconstructed)
                    print(f"  Successfully wrote clean sidecar to {target_path}")
                except Exception as e:
                    print(f"  Error writing sidecar {target_path}: {e}")

    print(f"\nSummary: Sanitized {total_cleaned_cues} cues across {total_episodes_affected} episodes.")

    # Check and clean LanceDB TM table if present
    tm_dir = backend_dir / "translation_memory" / "Solo Leveling_el"
    if tm_dir.exists():
        try:
            import lancedb
            db = lancedb.connect(str(tm_dir))
            tables = getattr(db.list_tables(), "tables", None)
            if tables is None:
                tables = db.table_names()
            if "translations" in tables:
                tbl = db.open_table("translations")
                records = tbl.to_arrow().to_pylist()
                tm_cleaned = 0
                for r in records:
                    t = r.get("target", "")
                    s = r.get("source", "")
                    cleaned_t = sanitize_translation_text(t, source_text=s, target_lang_code="el")
                    if cleaned_t != t:
                        r["target"] = cleaned_t
                        tm_cleaned += 1
                if tm_cleaned > 0:
                    print(f"Sanitized {tm_cleaned} records in LanceDB TM 'Solo Leveling_el'. Re-creating table...")
                    import pyarrow as pa
                    schema = tbl.schema
                    new_tbl = pa.Table.from_pylist(records, schema=schema)
                    db.create_table("translations", data=new_tbl, mode="overwrite")
                    print("LanceDB TM table successfully updated.")
                else:
                    print("LanceDB TM 'Solo Leveling_el' has no contaminated records.")
        except Exception as e:
            print(f"Note on TM update: {e}")

if __name__ == "__main__":
    repair_solo_leveling()
