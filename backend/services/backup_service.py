"""Backup and Migration Service for Omnisub.

Handles creating and importing portable migration bundles (.zip) between
server instances (e.g. migrating from a Windows workstation to a dedicated server).
Supports:
  - Online consistent SQLite backups (WAL-safe without locking live server)
  - SHA-256 integrity verification
  - Compact (without original media/subtitles) vs Full export modes
  - UNC-to-local path normalization and custom path prefix remapping
  - Atomic restoration with safety pre-import backups
"""

import hashlib
import json
import logging
import os
import re
import shutil
import socket
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from utils import paths

logger = logging.getLogger(__name__)

# Known SQLite database file names relative to DATA_DIR
KNOWN_DB_NAMES = (
    "omnisub.db",
    "omnisub_sessions.db",
    "autopilot.db",
    "subtitle_ownership.db",
    "data/autopilot.db",
    "data/subtitle_ownership.db",
)

# Known top-level configuration / state files
CONFIG_FILES = (
    "config.json",
    "rate_limiter_state.json",
    "release_version.json",
)


def _compute_sha256_and_size(path: Path) -> Tuple[str, int]:
    """Compute sha256 hexdigest and size in bytes for a file on disk."""
    hasher = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            hasher.update(chunk)
            size += len(chunk)
    return hasher.hexdigest(), size


def _backup_sqlite_db(src_path: Path, dst_temp_path: Path) -> None:
    """Safely snapshot a live SQLite database including WAL state using sqlite3 backup API."""
    dst_temp_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        src_conn = sqlite3.connect(f"file:{src_path.resolve()}?mode=ro", uri=True)
    except Exception:
        src_conn = sqlite3.connect(str(src_path.resolve()), timeout=30.0)

    dst_conn = sqlite3.connect(str(dst_temp_path))
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()


def _find_databases(data_dir: Path) -> List[Path]:
    """Find all existing database files in data_dir (including data/ subdirectory)."""
    found: List[Path] = []
    seen_resolved: Set[Path] = set()

    for rel_name in KNOWN_DB_NAMES:
        candidate = data_dir / rel_name
        if candidate.is_file() and candidate.resolve() not in seen_resolved:
            found.append(candidate)
            seen_resolved.add(candidate.resolve())

    # Also discover any extra .db files directly in data_dir or data_dir / "data"
    for search_dir in (data_dir, data_dir / "data"):
        if search_dir.is_dir():
            for item in search_dir.iterdir():
                if item.is_file() and item.suffix == ".db":
                    if not item.name.endswith(".bak") and item.resolve() not in seen_resolved:
                        found.append(item)
                        seen_resolved.add(item.resolve())

    return found


def normalize_path_for_local_server(
    path_str: str,
    remap_unc_to_local: bool = True,
    path_replacements: Optional[List[Dict[str, str]]] = None,
) -> str:
    r"""Normalize a path for the local server.

    1. Applies explicit prefix replacements ([{"from": old, "to": new}]) case-insensitively.
    2. If remap_unc_to_local is True, converts \\<server>\<drive>\<rest> -> <DRIVE>:\<rest>.
    """
    if not path_str or not isinstance(path_str, str):
        return path_str

    result = path_str

    # 1. Apply explicit path replacements
    if path_replacements:
        norm_result = result.replace("/", "\\")
        for rep in path_replacements:
            old_pfx = rep.get("from") or ""
            new_pfx = rep.get("to") or ""
            if not old_pfx:
                continue

            norm_old = old_pfx.replace("/", "\\").rstrip("\\")
            if norm_result.lower() == norm_old.lower():
                result = new_pfx
                norm_result = result.replace("/", "\\")
                break
            elif norm_result.lower().startswith(norm_old.lower() + "\\"):
                suffix = norm_result[len(norm_old) + 1:]
                sep = "\\" if ("\\" in new_pfx or ":" in new_pfx or "/" not in new_pfx) else "/"
                if new_pfx.endswith(("\\", "/")):
                    result = f"{new_pfx}{suffix}"
                else:
                    result = f"{new_pfx}{sep}{suffix}"
                norm_result = result.replace("/", "\\")
                break

    # 2. Remap UNC share drive to local drive letter if requested
    if remap_unc_to_local:
        # Matches: \\server\e\rest, //server/e/rest, \\server\e$\rest, etc.
        unc_pattern = re.compile(r'^[\\/]{2}([^\\/]+)[\\/]([a-zA-Z])\$?(?:[\\/](.*))?$')
        m = unc_pattern.match(result)
        if m:
            drive_letter = m.group(2).upper()
            rest = m.group(3) or ""
            rest_clean = rest.replace("/", "\\")
            if rest_clean:
                result = f"{drive_letter}:\\{rest_clean}"
            else:
                result = f"{drive_letter}:\\"

    return result


def _format_size(size_bytes: int) -> str:
    """Format bytes into a human-readable string."""
    val = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if val < 1024.0:
            return f"{val:.1f} {unit}"
        val /= 1024.0
    return f"{val:.1f} TB"


def get_backup_estimate(data_dir: Optional[Path] = None) -> dict:
    """Estimate size and counts for a migration bundle."""
    base_dir = Path(data_dir) if data_dir else paths.DATA_DIR
    projects_dir = base_dir / "projects"
    tm_dir = base_dir / "translation_memory"

    project_count = 0
    episode_count = 0
    compact_bytes = 0
    full_bytes = 0

    # Count projects and episodes
    if projects_dir.is_dir():
        for p in projects_dir.iterdir():
            if p.is_dir():
                project_count += 1
                # Project level files
                for p_file in ("project.json", "glossary.json"):
                    pf = p / p_file
                    if pf.is_file():
                        sz = pf.stat().st_size
                        compact_bytes += sz
                        full_bytes += sz

                ep_dir = p / "episodes"
                if ep_dir.is_dir():
                    for e in ep_dir.iterdir():
                        if e.is_dir():
                            episode_count += 1
                            for item in e.iterdir():
                                if not item.is_file():
                                    continue
                                name = item.name.lower()
                                if name.endswith(".bak") or "agent_workspace" in item.parts:
                                    continue
                                sz = item.stat().st_size
                                if name.startswith("original.") or name.startswith("original_backup."):
                                    full_bytes += sz
                                else:
                                    compact_bytes += sz
                                    full_bytes += sz

    # Databases
    databases = _find_databases(base_dir)
    db_rel_names = []
    for db in databases:
        try:
            rel = db.relative_to(base_dir).as_posix()
        except ValueError:
            rel = db.name
        db_rel_names.append(rel)
        sz = db.stat().st_size
        compact_bytes += sz
        full_bytes += sz

    # Configs and state files
    for cf in CONFIG_FILES:
        candidate = base_dir / cf
        if candidate.is_file():
            sz = candidate.stat().st_size
            compact_bytes += sz
            full_bytes += sz

    # Translation memory
    has_tm = False
    if tm_dir.is_dir():
        for root, _, files in os.walk(tm_dir):
            for f in files:
                has_tm = True
                sz = (Path(root) / f).stat().st_size
                compact_bytes += sz
                full_bytes += sz

    return {
        "project_count": project_count,
        "episode_count": episode_count,
        "compact_bytes_estimate": compact_bytes,
        "compact_size_bytes": compact_bytes,
        "compact_size_human": _format_size(compact_bytes),
        "full_bytes_estimate": full_bytes,
        "full_size_bytes": full_bytes,
        "full_size_human": _format_size(full_bytes),
        "databases": db_rel_names,
        "has_translation_memory": has_tm,
        "hostname": socket.gethostname(),
    }


def create_migration_bundle(
    output_path: Optional[Path] = None,
    mode: str = "compact",
    data_dir: Optional[Path] = None,
) -> Path:
    """Create a .zip migration bundle."""
    if mode not in ("compact", "full"):
        raise ValueError(f"Invalid mode '{mode}'. Must be 'compact' or 'full'.")

    base_dir = Path(data_dir) if data_dir else paths.DATA_DIR
    projects_dir = base_dir / "projects"
    tm_dir = base_dir / "translation_memory"

    if output_path is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_path = Path(tempfile.gettempdir()) / f"ombisub-migration-{mode}-{timestamp}.zip"
    else:
        output_path = Path(output_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Temporary directory to stage WAL-consistent database snapshots
    temp_dir = Path(tempfile.mkdtemp(prefix="ombisub_migration_"))
    try:
        # Map of zip_rel_path -> local disk Path
        files_to_pack: Dict[str, Path] = {}

        # 1. Consistent SQLite DB snapshots
        db_files = _find_databases(base_dir)
        for db in db_files:
            try:
                rel = db.relative_to(base_dir).as_posix()
            except ValueError:
                rel = db.name
            snap_file = temp_dir / "db_snaps" / rel
            _backup_sqlite_db(db, snap_file)
            zip_rel = f"databases/{rel}"
            files_to_pack[zip_rel] = snap_file

        # 2. Config & state files
        for cf in CONFIG_FILES:
            cand = base_dir / cf
            if cand.is_file():
                files_to_pack[cf] = cand

        # 3. Translation memory
        if tm_dir.is_dir():
            for root, _, files in os.walk(tm_dir):
                for f in files:
                    fp = Path(root) / f
                    rel_tm = fp.relative_to(base_dir).as_posix()
                    files_to_pack[rel_tm] = fp

        # 4. Projects & Episodes
        project_count = 0
        episode_count = 0
        if projects_dir.is_dir():
            for p in sorted(projects_dir.iterdir()):
                if not p.is_dir():
                    continue
                project_count += 1
                for pf_name in ("project.json", "glossary.json"):
                    pf = p / pf_name
                    if pf.is_file():
                        rel = pf.relative_to(base_dir).as_posix()
                        files_to_pack[rel] = pf

                ep_dir = p / "episodes"
                if ep_dir.is_dir():
                    for e in sorted(ep_dir.iterdir()):
                        if not e.is_dir():
                            continue
                        episode_count += 1
                        for item in sorted(e.iterdir()):
                            if not item.is_file():
                                continue
                            name = item.name.lower()
                            if name.endswith(".bak") or "agent_workspace" in item.parts:
                                continue

                            is_original = name.startswith("original.") or name.startswith("original_backup.")
                            if is_original and mode == "compact":
                                continue

                            rel = item.relative_to(base_dir).as_posix()
                            files_to_pack[rel] = item

        # Calculate manifest entries
        files_manifest: Dict[str, Dict[str, Any]] = {}
        for zip_rel, disk_path in files_to_pack.items():
            sha, sz = _compute_sha256_and_size(disk_path)
            files_manifest[zip_rel] = {"size": sz, "sha256": sha}

        manifest = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "hostname": socket.gethostname(),
            "mode": mode,
            "project_count": project_count,
            "episode_count": episode_count,
            "files": files_manifest,
        }

        # Write zip bundle
        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))
            for zip_rel, disk_path in files_to_pack.items():
                zf.write(disk_path, arcname=zip_rel)

        return output_path
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def inspect_migration_bundle(zip_path: Path) -> dict:
    """Validate zip & manifest.json, verify SHA-256 checksums of recorded entries."""
    zip_path = Path(zip_path)
    if not zip_path.is_file():
        raise ValueError(f"Migration bundle file does not exist: {zip_path}")

    try:
        zf = zipfile.ZipFile(zip_path, "r")
    except Exception as e:
        raise ValueError(f"Corrupt or invalid zip archive: {e}")

    with zf:
        if "manifest.json" not in zf.namelist():
            raise ValueError("Missing manifest.json in migration bundle")

        try:
            manifest_raw = zf.read("manifest.json").decode("utf-8")
            manifest = json.loads(manifest_raw)
        except Exception as e:
            raise ValueError(f"Could not parse manifest.json: {e}")

        files_meta = manifest.get("files", {})
        for zip_rel, expected in files_meta.items():
            if zip_rel not in zf.namelist():
                raise ValueError(f"Bundle is missing file recorded in manifest: {zip_rel}")

            with zf.open(zip_rel, "r") as f:
                hasher = hashlib.sha256()
                size = 0
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    hasher.update(chunk)
                    size += len(chunk)

            expected_sha = expected.get("sha256")
            expected_sz = expected.get("size")
            actual_sha = hasher.hexdigest()

            if expected_sz is not None and size != expected_sz:
                raise ValueError(f"Size mismatch for {zip_rel}: expected {expected_sz}, got {size}")
            if expected_sha and actual_sha.lower() != expected_sha.lower():
                raise ValueError(
                    f"Checksum mismatch for {zip_rel}: expected {expected_sha}, got {actual_sha}"
                )

        bundle_sha, _ = _compute_sha256_and_size(zip_path)
        return {
            "valid": True,
            "manifest": manifest,
            "hostname": manifest.get("hostname", "unknown"),
            "created_at": manifest.get("created_at", ""),
            "project_count": manifest.get("project_count", 0),
            "episode_count": manifest.get("episode_count", 0),
            "mode": manifest.get("mode", "compact"),
            "sha256": bundle_sha[:16],
            "sha256_verified": True,
        }


def import_migration_bundle(
    zip_path: Path,
    remap_unc_to_local: bool = True,
    path_replacements: Optional[List[Dict[str, str]]] = None,
    target_data_dir: Optional[Path] = None,
) -> dict:
    """Import a migration bundle into target_data_dir."""
    target_dir = Path(target_data_dir) if target_data_dir else paths.DATA_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    # 1. Verify bundle integrity first before touching target directory
    bundle_info = inspect_migration_bundle(zip_path)
    manifest = bundle_info.get("manifest", {})

    # 2. Safety pre-import backup of existing config.json and omnisub.db
    existing_cfg = target_dir / "config.json"
    if existing_cfg.is_file():
        shutil.copy2(existing_cfg, target_dir / "config.json.pre_import.bak")

    existing_db = target_dir / "omnisub.db"
    if existing_db.is_file():
        shutil.copy2(existing_db, target_dir / "omnisub.db.pre_import.bak")

    # 3. Extract files from zip
    target_resolved = target_dir.resolve()
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.namelist():
            if member == "manifest.json" or member.endswith("/"):
                continue

            # Determine extraction path
            if member.startswith("databases/"):
                rel = member[len("databases/"):]
                dest = target_dir / rel
            else:
                dest = target_dir / member

            # Prevent Zip Slip / path traversal
            dest_resolved = dest.resolve()
            try:
                dest_resolved.relative_to(target_resolved)
            except ValueError:
                raise ValueError(f"Path traversal detected in zip entry: {member}")

            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member, "r") as src_f, open(dest, "wb") as dst_f:
                shutil.copyfileobj(src_f, dst_f)

    # 4. Normalize paths across configs, metadata, and SQLite DBs
    remapped_count = 0

    def _remap(path_val: Any) -> Any:
        nonlocal remapped_count
        if not path_val or not isinstance(path_val, str):
            return path_val
        new_val = normalize_path_for_local_server(
            path_val,
            remap_unc_to_local=remap_unc_to_local,
            path_replacements=path_replacements,
        )
        if new_val != path_val:
            remapped_count += 1
        return new_val

    if remap_unc_to_local or path_replacements:
        # A. config.json
        cfg_file = target_dir / "config.json"
        if cfg_file.is_file():
            try:
                with open(cfg_file, "r", encoding="utf-8") as f:
                    cfg_data = json.load(f)
                cfg_modified = False
                for pkey in ("arr_path_mappings", "path_mappings"):
                    if pkey in cfg_data and isinstance(cfg_data[pkey], list):
                        for mapping in cfg_data[pkey]:
                            if isinstance(mapping, dict):
                                for lk in ("local", "local_path"):
                                    if lk in mapping and isinstance(mapping[lk], str):
                                        orig = mapping[lk]
                                        mapping[lk] = _remap(orig)
                                        if mapping[lk] != orig:
                                            cfg_modified = True
                if cfg_modified:
                    with open(cfg_file, "w", encoding="utf-8") as f:
                        json.dump(cfg_data, f, indent=2)
            except Exception as e:
                logger.warning(f"Error remapping paths in config.json: {e}")

        # B. Walk projects/*/episodes/*/metadata.json
        projects_dir = target_dir / "projects"
        if projects_dir.is_dir():
            for p in projects_dir.iterdir():
                if not p.is_dir():
                    continue
                ep_dir = p / "episodes"
                if not ep_dir.is_dir():
                    continue
                for e in ep_dir.iterdir():
                    if not e.is_dir():
                        continue
                    meta_path = e / "metadata.json"
                    if meta_path.is_file():
                        try:
                            with open(meta_path, "r", encoding="utf-8") as f:
                                meta = json.load(f)
                            meta_modified = False
                            for k in ("arr_media_path", "arr_sub_path", "arr_target_path", "media_path"):
                                if k in meta and isinstance(meta[k], str):
                                    orig = meta[k]
                                    meta[k] = _remap(orig)
                                    if meta[k] != orig:
                                        meta_modified = True

                            if "arr_targets" in meta and isinstance(meta["arr_targets"], list):
                                for tgt in meta["arr_targets"]:
                                    if isinstance(tgt, dict):
                                        for tk in ("target_path", "sub_path", "path"):
                                            if tk in tgt and isinstance(tgt[tk], str):
                                                orig = tgt[tk]
                                                tgt[tk] = _remap(orig)
                                                if tgt[tk] != orig:
                                                    meta_modified = True
                            if meta_modified:
                                with open(meta_path, "w", encoding="utf-8") as f:
                                    json.dump(meta, f, indent=2)
                        except Exception as e:
                            logger.warning(f"Error remapping paths in {meta_path}: {e}")

        # C. Update SQLite databases
        for db_file in _find_databases(target_dir):
            try:
                conn = sqlite3.connect(str(db_file), timeout=30.0)
                try:
                    tables = [
                        r[0] for r in conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()
                    ]

                    # 1) autopilot items table (either 'items' or 'autopilot_items')
                    for item_tbl in ("items", "autopilot_items"):
                        if item_tbl in tables:
                            cols = [
                                r[1] for r in conn.execute(f"PRAGMA table_info({item_tbl})").fetchall()
                            ]
                            if "media_path" in cols:
                                rows = conn.execute(
                                    f"SELECT id, media_path FROM {item_tbl} WHERE media_path IS NOT NULL"
                                ).fetchall()
                                for r_id, old_mp in rows:
                                    new_mp = _remap(old_mp)
                                    if new_mp != old_mp:
                                        conn.execute(
                                            f"UPDATE {item_tbl} SET media_path = ? WHERE id = ?",
                                            (new_mp, r_id),
                                        )

                            out_col = "outputs" if "outputs" in cols else ("outputs_json" if "outputs_json" in cols else None)
                            if out_col:
                                rows = conn.execute(
                                    f"SELECT id, {out_col} FROM {item_tbl} WHERE {out_col} IS NOT NULL"
                                ).fetchall()
                                for r_id, raw_out in rows:
                                    if not raw_out:
                                        continue
                                    try:
                                        data = json.loads(raw_out)
                                        obj_changed = False

                                        def _remap_obj(val: Any) -> Any:
                                            nonlocal obj_changed
                                            if isinstance(val, str):
                                                nv = _remap(val)
                                                if nv != val:
                                                    obj_changed = True
                                                return nv
                                            elif isinstance(val, list):
                                                return [_remap_obj(x) for x in val]
                                            elif isinstance(val, dict):
                                                return {k: _remap_obj(v) for k, v in val.items()}
                                            return val

                                        new_data = _remap_obj(data)
                                        if obj_changed:
                                            conn.execute(
                                                f"UPDATE {item_tbl} SET {out_col} = ? WHERE id = ?",
                                                (json.dumps(new_data), r_id),
                                            )
                                    except Exception:
                                        pass

                    # 2) subtitle_ownership.db ('owned_files')
                    if "owned_files" in tables:
                        cols = [r[1] for r in conn.execute("PRAGMA table_info(owned_files)").fetchall()]
                        if "path" in cols:
                            has_disp = "display_path" in cols
                            query = (
                                "SELECT path, display_path, sha1, size, project_name, episode_name, written_at FROM owned_files"
                                if has_disp
                                else "SELECT path, sha1, size, project_name, episode_name, written_at FROM owned_files"
                            )
                            rows = conn.execute(query).fetchall()
                            for r in rows:
                                if has_disp:
                                    old_p, disp_p, sha1, sz, prj, ep, wrt = r
                                    new_p = _remap(old_p)
                                    new_disp = _remap(disp_p)
                                    if new_p != old_p or new_disp != disp_p:
                                        conn.execute("DELETE FROM owned_files WHERE path = ?", (old_p,))
                                        conn.execute(
                                            "INSERT OR REPLACE INTO owned_files "
                                            "(path, display_path, sha1, size, project_name, episode_name, written_at) "
                                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                                            (new_p, new_disp, sha1, sz, prj, ep, wrt),
                                        )
                                else:
                                    old_p, sha1, sz, prj, ep, wrt = r
                                    new_p = _remap(old_p)
                                    if new_p != old_p:
                                        conn.execute("DELETE FROM owned_files WHERE path = ?", (old_p,))
                                        conn.execute(
                                            "INSERT OR REPLACE INTO owned_files "
                                            "(path, sha1, size, project_name, episode_name, written_at) "
                                            "VALUES (?, ?, ?, ?, ?, ?)",
                                            (new_p, sha1, sz, prj, ep, wrt),
                                        )

                    # 3) media_probe_cache ('media_path')
                    if "media_probe_cache" in tables:
                        cols = [r[1] for r in conn.execute("PRAGMA table_info(media_probe_cache)").fetchall()]
                        if "media_path" in cols:
                            rows = conn.execute("SELECT media_path, fingerprint FROM media_probe_cache").fetchall()
                            for old_mp, fp in rows:
                                new_mp = _remap(old_mp)
                                if new_mp != old_mp:
                                    conn.execute(
                                        "UPDATE media_probe_cache SET media_path = ? WHERE media_path = ? AND fingerprint = ?",
                                        (new_mp, old_mp, fp),
                                    )

                    # 4) translation_blacklist ('file_path')
                    if "translation_blacklist" in tables:
                        cols = [r[1] for r in conn.execute("PRAGMA table_info(translation_blacklist)").fetchall()]
                        if "file_path" in cols:
                            rows = conn.execute("SELECT file_path FROM translation_blacklist").fetchall()
                            for (old_fp,) in rows:
                                new_fp = _remap(old_fp)
                                if new_fp != old_fp:
                                    conn.execute(
                                        "UPDATE translation_blacklist SET file_path = ? WHERE file_path = ?",
                                        (new_fp, old_fp),
                                    )

                    conn.commit()
                finally:
                    conn.close()
            except Exception as e:
                logger.warning(f"Error remapping database {db_file}: {e}")

    # Count restored projects & episodes
    projects_restored = 0
    episodes_restored = 0
    target_projects = target_dir / "projects"
    if target_projects.is_dir():
        for p in target_projects.iterdir():
            if p.is_dir():
                projects_restored += 1
                ep_dir = p / "episodes"
                if ep_dir.is_dir():
                    episodes_restored += sum(1 for e in ep_dir.iterdir() if e.is_dir())

    return {
        "status": "success",
        "success": True,
        "message": "Server restored and migrated successfully.",
        "source_hostname": manifest.get("hostname", "unknown"),
        "projects_restored": projects_restored,
        "restored_projects": projects_restored,
        "episodes_restored": episodes_restored,
        "restored_episodes": episodes_restored,
        "paths_remapped": remapped_count,
        "mode": manifest.get("mode", "compact"),
        "restart_required": True,
    }
