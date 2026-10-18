"""Unit and Integration Tests for Omnisub Backup, Migration & CLI."""

import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.backup import router as backup_router
from services.backup_service import (
    create_migration_bundle,
    get_backup_estimate,
    import_migration_bundle,
    inspect_migration_bundle,
    normalize_path_for_local_server,
)


@pytest.fixture
def sample_data_dir(tmp_path: Path) -> Path:
    """Fixture creating a realistic Omnisub data directory structure."""
    data_dir = tmp_path / "omnisub_data"
    data_dir.mkdir(parents=True)

    # 1. Config & State
    cfg = {
        "gemini_api_key": "obfuscated_dummy_key",
        "arr_path_mappings": [
            {
                "remote": r"\\Morpiceserver\e\Shows",
                "local": r"\\Morpiceserver\e\Shows",
            }
        ],
    }
    (data_dir / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    (data_dir / "rate_limiter_state.json").write_text(
        json.dumps({"tokens": 100}), encoding="utf-8"
    )

    # 2. SQLite Databases
    # omnisub.db with WAL mode
    db_file = data_dir / "omnisub.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE translation_queue (id TEXT PRIMARY KEY, project_name TEXT)")
    conn.execute("INSERT INTO translation_queue VALUES ('q1', 'Attack on Titan')")
    conn.commit()
    conn.close()

    # autopilot.db
    ap_db = data_dir / "autopilot.db"
    conn = sqlite3.connect(str(ap_db))
    conn.execute(
        "CREATE TABLE items (id TEXT PRIMARY KEY, media_path TEXT, outputs TEXT)"
    )
    conn.execute(
        "INSERT INTO items VALUES ('item1', ?, ?)",
        (
            r"\\Morpiceserver\e\Shows\Attack on Titan\S01E01.mkv",
            json.dumps({"srt": r"\\Morpiceserver\e\Shows\Attack on Titan\S01E01.el.srt"}),
        ),
    )
    conn.commit()
    conn.close()

    # subtitle_ownership.db
    own_db = data_dir / "subtitle_ownership.db"
    conn = sqlite3.connect(str(own_db))
    conn.execute(
        "CREATE TABLE owned_files ("
        "path TEXT PRIMARY KEY, display_path TEXT, sha1 TEXT, size INTEGER, "
        "project_name TEXT, episode_name TEXT, written_at TEXT)"
    )
    conn.execute(
        "INSERT INTO owned_files VALUES (?, ?, 'fake_sha1', 1024, 'Attack on Titan', 'S01E01', '2026-10-18T00:00:00Z')",
        (
            r"\\morpiceserver\e\shows\attack on titan\s01e01.el.srt",
            r"\\Morpiceserver\e\Shows\Attack on Titan\S01E01.el.srt",
        ),
    )
    conn.commit()
    conn.close()

    # 3. Translation Memory
    tm_dir = data_dir / "translation_memory"
    tm_dir.mkdir()
    (tm_dir / "el_en.json").write_text(json.dumps({"Hello": "Γεια"}), encoding="utf-8")

    # 4. Projects & Episodes
    proj_dir = data_dir / "projects" / "Attack on Titan"
    ep_dir = proj_dir / "episodes" / "S01E01"
    ep_dir.mkdir(parents=True)

    (proj_dir / "project.json").write_text(
        json.dumps({"title": "Attack on Titan"}), encoding="utf-8"
    )
    (proj_dir / "glossary.json").write_text(
        json.dumps({"Titan": "Τιτάνας"}), encoding="utf-8"
    )

    metadata = {
        "arr_media_path": r"\\Morpiceserver\e\Shows\Attack on Titan\S01E01.mkv",
        "arr_sub_path": r"\\Morpiceserver\e\Shows\Attack on Titan\S01E01.en.srt",
        "arr_target_path": r"\\Morpiceserver\e\Shows\Attack on Titan\S01E01.el.srt",
        "arr_targets": [
            {"target_path": r"\\Morpiceserver\e\Shows\Attack on Titan\S01E01.el.srt"}
        ],
    }
    (ep_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    (ep_dir / "data.json").write_text(
        json.dumps([{"index": 1, "text": "Shingeki"}]), encoding="utf-8"
    )
    (ep_dir / "original.srt").write_text("1\n00:00:01,000 --> 00:00:03,000\nHello\n", encoding="utf-8")
    (ep_dir / "original_backup.srt").write_text("backup srt content", encoding="utf-8")

    # Workspace & backup files that should be excluded
    ws_dir = ep_dir / "agent_workspace"
    ws_dir.mkdir()
    (ws_dir / "temp.log").write_text("temporary log", encoding="utf-8")
    (ep_dir / "metadata.json.bak").write_text("backup content", encoding="utf-8")

    return data_dir


def test_backup_estimate(sample_data_dir: Path):
    """Verify get_backup_estimate accurately walks projects, episodes, and databases."""
    est = get_backup_estimate(data_dir=sample_data_dir)

    assert est["project_count"] == 1
    assert est["episode_count"] == 1
    assert est["has_translation_memory"] is True
    assert "omnisub.db" in est["databases"]
    assert "autopilot.db" in est["databases"]
    assert "subtitle_ownership.db" in est["databases"]

    # compact should exclude original.srt, so full_bytes must be strictly greater than compact_bytes
    assert est["full_bytes_estimate"] > est["compact_bytes_estimate"]
    diff = est["full_bytes_estimate"] - est["compact_bytes_estimate"]
    orig_size = (sample_data_dir / "projects/Attack on Titan/episodes/S01E01/original.srt").stat().st_size
    orig_bak_size = (sample_data_dir / "projects/Attack on Titan/episodes/S01E01/original_backup.srt").stat().st_size
    assert diff == orig_size + orig_bak_size


def test_compact_vs_full_export(sample_data_dir: Path, tmp_path: Path):
    """Verify compact mode excludes original.* while full mode includes them."""
    compact_zip = tmp_path / "compact.zip"
    full_zip = tmp_path / "full.zip"

    create_migration_bundle(output_path=compact_zip, mode="compact", data_dir=sample_data_dir)
    create_migration_bundle(output_path=full_zip, mode="full", data_dir=sample_data_dir)

    with zipfile.ZipFile(compact_zip, "r") as zf:
        names = zf.namelist()
        assert "manifest.json" in names
        assert "config.json" in names
        assert "databases/omnisub.db" in names
        assert "databases/autopilot.db" in names
        assert "databases/subtitle_ownership.db" in names
        assert "projects/Attack on Titan/project.json" in names
        assert "projects/Attack on Titan/glossary.json" in names
        assert "projects/Attack on Titan/episodes/S01E01/metadata.json" in names
        assert "projects/Attack on Titan/episodes/S01E01/data.json" in names

        # compact exclusions
        assert "projects/Attack on Titan/episodes/S01E01/original.srt" not in names
        assert "projects/Attack on Titan/episodes/S01E01/original_backup.srt" not in names
        assert not any("agent_workspace" in n for n in names)
        assert not any(n.endswith(".bak") for n in names)

    with zipfile.ZipFile(full_zip, "r") as zf:
        names = zf.namelist()
        assert "projects/Attack on Titan/episodes/S01E01/original.srt" in names
        assert "projects/Attack on Titan/episodes/S01E01/original_backup.srt" in names
        assert not any("agent_workspace" in n for n in names)
        assert not any(n.endswith(".bak") for n in names)


def test_online_sqlite_backup_consistency_and_sha256(sample_data_dir: Path, tmp_path: Path):
    """Verify SQLite backup is WAL-safe, consistent, and passes inspect checksum verification."""
    bundle_zip = tmp_path / "backup.zip"
    create_migration_bundle(output_path=bundle_zip, mode="compact", data_dir=sample_data_dir)

    # inspect_migration_bundle must verify all sha256 checksums successfully
    inspection = inspect_migration_bundle(bundle_zip)
    assert inspection["valid"] is True
    manifest = inspection["manifest"]
    assert manifest["project_count"] == 1
    assert manifest["episode_count"] == 1
    assert "databases/omnisub.db" in manifest["files"]

    # Verify extracted DB is healthy
    with zipfile.ZipFile(bundle_zip, "r") as zf:
        zf.extract("databases/omnisub.db", tmp_path / "extracted")

    extracted_db = tmp_path / "extracted" / "databases" / "omnisub.db"
    conn = sqlite3.connect(str(extracted_db))
    rows = conn.execute("SELECT project_name FROM translation_queue").fetchall()
    assert rows == [("Attack on Titan",)]
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    assert integrity == "ok"
    conn.close()


def test_corrupt_zip_and_bad_checksum_rejection(sample_data_dir: Path, tmp_path: Path):
    """Verify corrupt bundle or bad checksum is rejected without modifying target directory."""
    bundle_zip = tmp_path / "tampered.zip"
    create_migration_bundle(output_path=bundle_zip, mode="compact", data_dir=sample_data_dir)

    # 1. Tamper with manifest by putting a bogus checksum
    with zipfile.ZipFile(bundle_zip, "r") as zf:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        files = {name: zf.read(name) for name in zf.namelist() if name != "manifest.json"}

    first_file = next(iter(manifest["files"]))
    manifest["files"][first_file]["sha256"] = "0" * 64

    with zipfile.ZipFile(bundle_zip, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for name, content in files.items():
            zf.writestr(name, content)

    # inspect must raise ValueError
    with pytest.raises(ValueError, match="Checksum mismatch"):
        inspect_migration_bundle(bundle_zip)

    # import must reject before modifying target directory
    target_dir = tmp_path / "target_dir"
    target_dir.mkdir()
    canary = target_dir / "target_marker.txt"
    canary.write_text("untouched")

    with pytest.raises(ValueError, match="Checksum mismatch"):
        import_migration_bundle(bundle_zip, target_data_dir=target_dir)

    assert canary.read_text() == "untouched"
    assert not (target_dir / "config.json").exists()


def test_normalize_path_for_local_server():
    """Verify path normalization, prefix replacements, and UNC share conversion."""
    # 1. Standard UNC drive share
    unc_path = r"\\Morpiceserver\e\Shows\Attack on Titan\S01E01.mkv"
    assert normalize_path_for_local_server(unc_path, remap_unc_to_local=True) == (
        r"E:\Shows\Attack on Titan\S01E01.mkv"
    )

    # 2. Forward-slash UNC path
    forward_unc = "//Morpiceserver/e/Shows/Attack on Titan/S01E01.mkv"
    assert normalize_path_for_local_server(forward_unc, remap_unc_to_local=True) == (
        r"E:\Shows\Attack on Titan\S01E01.mkv"
    )

    # 3. Administrative UNC share with dollar sign
    admin_unc = r"\\Morpiceserver\e$\Shows\Attack on Titan\S01E01.mkv"
    assert normalize_path_for_local_server(admin_unc, remap_unc_to_local=True) == (
        r"E:\Shows\Attack on Titan\S01E01.mkv"
    )

    # 4. Bare root UNC share
    root_unc = r"\\Morpiceserver\e"
    assert normalize_path_for_local_server(root_unc, remap_unc_to_local=True) == "E:\\"

    # 5. Non-drive UNC share (remains unchanged when no explicit replacement matches)
    share_unc = r"\\nas\mediashare\movie.mkv"
    assert normalize_path_for_local_server(share_unc, remap_unc_to_local=True) == share_unc

    # 6. Explicit path replacement
    replacements = [{"from": r"\\nas\mediashare", "to": r"M:\Media"}]
    assert normalize_path_for_local_server(
        share_unc, remap_unc_to_local=True, path_replacements=replacements
    ) == r"M:\Media\movie.mkv"

    # 7. Local path unchanged
    local_path = r"D:\Media\Video.mkv"
    assert normalize_path_for_local_server(local_path, remap_unc_to_local=True) == local_path


def test_import_remap_unc_to_local_all_locations(sample_data_dir: Path, tmp_path: Path):
    """Verify import restores data, creates pre-import backups, and remaps all paths."""
    bundle_zip = tmp_path / "bundle.zip"
    create_migration_bundle(output_path=bundle_zip, mode="compact", data_dir=sample_data_dir)

    target_dir = tmp_path / "imported_server_dir"
    target_dir.mkdir()

    # Pre-populate target dir with existing config.json and omnisub.db to test .pre_import.bak creation
    (target_dir / "config.json").write_text(json.dumps({"old": "config"}), encoding="utf-8")
    old_db = target_dir / "omnisub.db"
    conn = sqlite3.connect(str(old_db))
    conn.execute("CREATE TABLE old_table (x INTEGER)")
    conn.commit()
    conn.close()

    summary = import_migration_bundle(
        zip_path=bundle_zip,
        remap_unc_to_local=True,
        target_data_dir=target_dir,
    )

    # Verify summary
    assert summary["status"] == "success"
    assert summary["projects_restored"] == 1
    assert summary["episodes_restored"] == 1
    assert summary["paths_remapped"] > 0
    assert summary["restart_required"] is True

    # 1. Verify safety backups
    assert (target_dir / "config.json.pre_import.bak").is_file()
    assert (target_dir / "omnisub.db.pre_import.bak").is_file()

    # 2. Verify config.json remapping
    new_cfg = json.loads((target_dir / "config.json").read_text(encoding="utf-8"))
    assert new_cfg["arr_path_mappings"][0]["local"] == r"E:\Shows"

    # 3. Verify metadata.json remapping
    ep_meta_file = target_dir / "projects/Attack on Titan/episodes/S01E01/metadata.json"
    ep_meta = json.loads(ep_meta_file.read_text(encoding="utf-8"))
    assert ep_meta["arr_media_path"] == r"E:\Shows\Attack on Titan\S01E01.mkv"
    assert ep_meta["arr_sub_path"] == r"E:\Shows\Attack on Titan\S01E01.en.srt"
    assert ep_meta["arr_target_path"] == r"E:\Shows\Attack on Titan\S01E01.el.srt"
    assert ep_meta["arr_targets"][0]["target_path"] == r"E:\Shows\Attack on Titan\S01E01.el.srt"

    # 4. Verify autopilot.db remapping
    conn = sqlite3.connect(str(target_dir / "autopilot.db"))
    row = conn.execute("SELECT media_path, outputs FROM items WHERE id='item1'").fetchone()
    assert row[0] == r"E:\Shows\Attack on Titan\S01E01.mkv"
    outputs = json.loads(row[1])
    assert outputs["srt"] == r"E:\Shows\Attack on Titan\S01E01.el.srt"
    conn.close()

    # 5. Verify subtitle_ownership.db remapping
    conn = sqlite3.connect(str(target_dir / "subtitle_ownership.db"))
    row = conn.execute("SELECT path, display_path FROM owned_files").fetchone()
    assert r"e:\shows\attack on titan\s01e01.el.srt" in row[0].lower()
    assert row[1] == r"E:\Shows\Attack on Titan\S01E01.el.srt"
    conn.close()


def test_cli_migrate_server_roundtrip(sample_data_dir: Path, tmp_path: Path):
    """Verify scripts/migrate_server.py CLI export, inspect, and import commands."""
    cli_script = Path(__file__).resolve().parent.parent.parent / "scripts" / "migrate_server.py"
    assert cli_script.is_file()

    bundle_path = tmp_path / "cli_backup.zip"
    target_data_dir = tmp_path / "cli_target_data"

    # 1. CLI Export
    res = subprocess.run(
        [
            sys.executable,
            str(cli_script),
            "export",
            "--output",
            str(bundle_path),
            "--mode",
            "compact",
            "--data-dir",
            str(sample_data_dir),
        ],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Export CLI failed:\nStdout: {res.stdout}\nStderr: {res.stderr}"
    assert bundle_path.is_file()

    # 2. CLI Inspect
    res = subprocess.run(
        [sys.executable, str(cli_script), "inspect", "--input", str(bundle_path)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Inspect CLI failed:\nStdout: {res.stdout}\nStderr: {res.stderr}"
    assert "Bundle integrity VERIFIED" in res.stdout

    # 3. CLI Import
    res = subprocess.run(
        [
            sys.executable,
            str(cli_script),
            "import",
            "--input",
            str(bundle_path),
            "--remap-unc-to-local",
            "--target-dir",
            str(target_data_dir),
        ],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Import CLI failed:\nStdout: {res.stdout}\nStderr: {res.stderr}"
    assert "Import completed successfully" in res.stdout

    # Verify target directory has imported data
    assert (target_data_dir / "config.json").is_file()
    assert (target_data_dir / "projects/Attack on Titan/project.json").is_file()


def test_router_backup_endpoints(sample_data_dir: Path, monkeypatch):
    """Verify FastAPI backup endpoints (/api/backup/estimate, export, inspect, import)."""
    from utils import paths

    monkeypatch.setattr(paths, "DATA_DIR", sample_data_dir)
    monkeypatch.setattr(paths, "PROJECTS_DIR", sample_data_dir / "projects")

    app = FastAPI()
    app.include_router(backup_router)
    client = TestClient(app)

    # 1. GET /api/backup/estimate
    resp = client.get("/api/backup/estimate")
    assert resp.status_code == 200
    data = resp.json()
    assert data["project_count"] == 1
    assert data["episode_count"] == 1

    # 2. GET /api/backup/export
    resp = client.get("/api/backup/export?mode=compact")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    zip_bytes = resp.content
    assert len(zip_bytes) > 0

    # 3. POST /api/backup/inspect
    resp = client.post(
        "/api/backup/inspect",
        files={"file": ("backup.zip", zip_bytes, "application/zip")},
    )
    assert resp.status_code == 200
    inspect_data = resp.json()
    assert inspect_data["valid"] is True
    assert inspect_data["manifest"]["project_count"] == 1

    # 4. POST /api/backup/import
    resp = client.post(
        "/api/backup/import",
        files={"file": ("backup.zip", zip_bytes, "application/zip")},
        data={"remap_unc_to_local": "true", "path_replacements_json": "[]"},
    )
    assert resp.status_code == 200
    import_data = resp.json()
    assert import_data["status"] == "success"
    assert import_data["projects_restored"] == 1


def test_import_replaces_a_live_wal_database_consistently(sample_data_dir: Path, tmp_path: Path):
    """The server keeps omnisub.db open (WAL) while a bundle is imported: no stale WAL content survives."""
    bundle_zip = tmp_path / "bundle.zip"
    create_migration_bundle(output_path=bundle_zip, mode="full", data_dir=sample_data_dir)
    target_dir = tmp_path / "live_server"
    target_dir.mkdir()

    live = sqlite3.connect(str(target_dir / "omnisub.db"))
    live.execute("PRAGMA journal_mode=WAL")
    live.execute("CREATE TABLE stale (x INTEGER)")
    live.executemany("INSERT INTO stale VALUES (?)", [(i,) for i in range(500)])
    live.commit()  # sits in omnisub.db-wal, not checkpointed
    try:
        import_migration_bundle(zip_path=bundle_zip, remap_unc_to_local=True, target_data_dir=target_dir)
        seen_live = {r[0] for r in live.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        live.close()

    fresh = sqlite3.connect(str(target_dir / "omnisub.db"))
    try:
        assert fresh.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        seen_fresh = {r[0] for r in fresh.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert fresh.execute("SELECT project_name FROM translation_queue").fetchall() == [("Attack on Titan",)]
    finally:
        fresh.close()
    assert "stale" not in seen_live and "stale" not in seen_fresh
    assert "translation_queue" in seen_live

    snap = sqlite3.connect(str(target_dir / "omnisub.db.pre_import.bak"))
    try:
        assert snap.execute("SELECT COUNT(*) FROM stale").fetchone()[0] == 500
    finally:
        snap.close()
    assert not any(p.name.startswith(".migration_import_") for p in target_dir.iterdir())


def test_import_swaps_translation_memory_instead_of_merging(sample_data_dir: Path, tmp_path: Path):
    """Two LanceDB datasets must never be mixed: the old one is set aside whole."""
    bundle_zip = tmp_path / "bundle.zip"
    create_migration_bundle(output_path=bundle_zip, mode="compact", data_dir=sample_data_dir)
    target_dir = tmp_path / "server_with_tm"
    stale = target_dir / "translation_memory" / "old_show_el.lance" / "data" / "fragment.lance"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old")

    import_migration_bundle(zip_path=bundle_zip, target_data_dir=target_dir)

    assert not (target_dir / "translation_memory" / "old_show_el.lance").exists()
    assert (target_dir / "translation_memory").is_dir()
    assert (target_dir / "translation_memory.pre_import.bak" / "old_show_el.lance" / "data" / "fragment.lance").is_file()


def test_one_time_download_link(sample_data_dir: Path, monkeypatch):
    """The browser downloads through a link minted by an authenticated call; the link works once."""
    from utils import paths

    monkeypatch.setattr(paths, "DATA_DIR", sample_data_dir)
    monkeypatch.setattr(paths, "PROJECTS_DIR", sample_data_dir / "projects")
    app = FastAPI()
    app.include_router(backup_router)
    client = TestClient(app)

    assert client.post("/api/backup/export-link?mode=everything").status_code == 400
    link = client.post("/api/backup/export-link").json()
    assert link["mode"] == "full"

    resp = client.get(link["url"])
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert "projects/Attack on Titan/episodes/S01E01/original.srt" in zf.namelist()

    assert client.get(link["url"]).status_code == 404  # used up


def test_download_link_path_is_exempt_from_the_api_key_gate():
    import main
    assert "/api/backup/download/abc".startswith(main._AUTH_EXEMPT_PREFIXES)
    assert not "/api/backup/export".startswith(main._AUTH_EXEMPT_PREFIXES)
