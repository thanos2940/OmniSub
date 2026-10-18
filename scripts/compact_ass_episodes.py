"""
Safe Batch Compaction Script for ASS Episodes (D-02).

Strips legacy passthrough bloat (vector drawings and syllable karaoke events)
from episode data.json files across projects, while leaving original.ass untouched.
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional, Union

# Add backend directory to sys.path so utils can be imported if run directly
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

logger = logging.getLogger("compact_ass_episodes")


def _write_json_atomic(path: Path, data: Any) -> None:
    try:
        from utils.storage_modules.atomic_io import write_json_atomic
        write_json_atomic(path, data)
        return
    except ImportError:
        pass
    import time
    temp_path = path.with_suffix(f"{path.suffix}.tmp.{os.getpid()}")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        for attempt in range(20):
            try:
                temp_path.replace(path)
                break
            except (PermissionError, OSError):
                if attempt == 19:
                    raise
                time.sleep(0.005 * (1.3 ** attempt))
    except Exception:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass
        raise


def compact_library(
    projects_dir: Optional[Union[Path, str]] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Batch compacts ASS episodes by removing drawing and karaoke bloat from data.json.

    Returns metrics on episodes compacted, rows removed, and bytes saved.
    """
    if projects_dir is None:
        try:
            from utils.paths import PROJECTS_DIR
            projects_dir = PROJECTS_DIR
        except Exception:
            projects_dir = Path("backend/projects")

    projects_path = Path(projects_dir).resolve()
    if not projects_path.exists():
        return {
            "episodes_compacted": 0,
            "rows_removed": 0,
            "bytes_before": 0,
            "bytes_after": 0,
            "bytes_saved": 0,
            "dry_run": dry_run,
        }

    episodes_compacted = 0
    rows_removed = 0
    bytes_before = 0
    bytes_after = 0

    # Walk projects_path/*/episodes/*
    for proj_dir in sorted(projects_path.iterdir()):
        if not proj_dir.is_dir():
            continue
        episodes_dir = proj_dir / "episodes"
        if not episodes_dir.is_dir():
            continue

        for ep_dir in sorted(episodes_dir.iterdir()):
            if not ep_dir.is_dir():
                continue

            has_ass = (ep_dir / "original.ass").exists() or (ep_dir / "original.ssa").exists()
            data_file = ep_dir / "data.json"
            if not has_ass or not data_file.exists():
                continue

            try:
                size_before = data_file.stat().st_size
                with open(data_file, "r", encoding="utf-8") as f:
                    content = json.load(f)

                rows = content if isinstance(content, list) else content.get("data", [])
                if not isinstance(rows, list):
                    continue

                def _is_ass_bloat(r: Dict[str, Any]) -> bool:
                    ass = r.get("_ass")
                    if not isinstance(ass, dict):
                        return False
                    if not ass.get("translatable", True) and (ass.get("kind") or ass.get("reason")) in ("drawing", "karaoke", "empty"):
                        return True
                    return False

                bloat_count = sum(1 for r in rows if _is_ass_bloat(r))

                if bloat_count == 0:
                    if not dry_run:
                        meta_file = ep_dir / "metadata.json"
                        if meta_file.exists():
                            try:
                                with open(meta_file, "r", encoding="utf-8") as mf:
                                    meta = json.load(mf)
                                cmap = meta.get("clean_to_orig_map")
                                if isinstance(cmap, dict) and len(cmap) != len(rows):
                                    meta["line_count"] = len(rows)
                                    meta.pop("clean_to_orig_map", None)
                                    _write_json_atomic(meta_file, meta)
                            except Exception:
                                pass
                    continue

                kept_rows = [r for r in rows if not _is_ass_bloat(r)]

                # Renumber kept rows
                for i, r in enumerate(kept_rows, start=1):
                    r["id"] = str(i)

                episodes_compacted += 1
                rows_removed += bloat_count
                bytes_before += size_before

                if not dry_run:
                    _write_json_atomic(data_file, {"data": kept_rows})
                    size_after = data_file.stat().st_size
                    bytes_after += size_after

                    meta_file = ep_dir / "metadata.json"
                    if meta_file.exists():
                        try:
                            with open(meta_file, "r", encoding="utf-8") as mf:
                                meta = json.load(mf)
                            meta["line_count"] = len(kept_rows)
                            meta.pop("clean_to_orig_map", None)
                            _write_json_atomic(meta_file, meta)
                        except Exception as e:
                            logger.warning(f"Failed to update metadata for {ep_dir.name}: {e}")
                else:
                    # Estimate post-compaction size
                    size_after = len(json.dumps({"data": kept_rows}, ensure_ascii=False, indent=2).encode("utf-8"))
                    bytes_after += size_after

            except Exception as e:
                logger.error(f"Error compacting {ep_dir}: {e}")

    bytes_saved = max(0, bytes_before - bytes_after)
    return {
        "episodes_compacted": episodes_compacted,
        "rows_removed": rows_removed,
        "bytes_before": bytes_before,
        "bytes_after": bytes_after,
        "bytes_saved": bytes_saved,
        "dry_run": dry_run,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Compact ASS episode data.json by stripping passthrough bloat.")
    parser.add_argument("--projects-dir", type=str, default=None, help="Path to projects directory")
    parser.add_argument("--dry-run", action="store_true", help="Report savings without modifying files")
    args = parser.parse_args()

    results = compact_library(projects_dir=args.projects_dir, dry_run=args.dry_run)
    print(json.dumps(results, indent=2))
