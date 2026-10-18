#!/usr/bin/env python3
r"""Omnisub Server Migration CLI Tool.

Supports:
  - export: Create a portable migration bundle (.zip) in full (server move) or compact mode
  - inspect: Validate bundle checksums and view manifest details
  - import: Restore bundle to target server and optionally remap UNC / custom paths

Examples:
  python scripts/migrate_server.py export --output backup.zip
  python scripts/migrate_server.py inspect --input backup.zip
  python scripts/migrate_server.py import --input backup.zip --remap-unc-to-local
  python scripts/migrate_server.py import --input backup.zip --replace "\\\\Morpiceserver\\e=E:\\"
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Add backend directory to sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    from services.backup_service import (
        create_migration_bundle,
        get_backup_estimate,
        import_migration_bundle,
        inspect_migration_bundle,
    )
except ImportError as e:
    print(f"Error importing OmbiSub backend services: {e}", file=sys.stderr)
    sys.exit(1)


def cmd_export(args: argparse.Namespace) -> int:
    output_path = Path(args.output) if args.output else None
    data_dir = Path(args.data_dir) if args.data_dir else None

    print(f"[*] Estimating backup size (mode: {args.mode})...")
    estimate = get_backup_estimate(data_dir=data_dir)
    est_bytes = (
        estimate["compact_bytes_estimate"]
        if args.mode == "compact"
        else estimate["full_bytes_estimate"]
    )
    est_mb = est_bytes / (1024 * 1024)
    print(
        f"[*] Found {estimate['project_count']} projects, {estimate['episode_count']} episodes, "
        f"{len(estimate['databases'])} databases. Estimated size: ~{est_mb:.2f} MB"
    )

    print(f"[*] Packaging migration bundle ({args.mode} mode)...")
    bundle_path = create_migration_bundle(
        output_path=output_path, mode=args.mode, data_dir=data_dir
    )
    actual_sz_mb = bundle_path.stat().st_size / (1024 * 1024)
    print(f"[+] Bundle successfully created: {bundle_path} ({actual_sz_mb:.2f} MB)")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    print(f"[*] Inspecting bundle: {input_path}")
    try:
        info = inspect_migration_bundle(input_path)
    except Exception as e:
        print(f"[-] Integrity check FAILED: {e}", file=sys.stderr)
        return 1

    manifest = info.get("manifest", {})
    files = manifest.get("files", {})
    print("[+] Bundle integrity VERIFIED: SHA-256 checksums match.")
    print(f"    Schema version : {manifest.get('schema_version')}")
    print(f"    Created at     : {manifest.get('created_at')}")
    print(f"    Source host    : {manifest.get('hostname')}")
    print(f"    Mode           : {manifest.get('mode')}")
    print(f"    Projects       : {manifest.get('project_count')}")
    print(f"    Episodes       : {manifest.get('episode_count')}")
    print(f"    Files packed   : {len(files)}")
    return 0


def _running_server(data_dir: Path) -> int:
    """PID of a live OmbiSub server that owns data_dir, else 0."""
    import run_server
    try:
        pid = int(run_server._read_lock(data_dir / run_server.LOCK_NAME).get("pid") or 0)
    except (TypeError, ValueError):
        return 0
    return pid if pid and pid != os.getpid() and run_server._pid_alive(pid) else 0


def cmd_import(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    target_dir = Path(args.target_dir) if args.target_dir else None

    from utils import paths
    pid = _running_server(target_dir or paths.DATA_DIR)
    if pid and not args.allow_running:
        print(f"[-] OmbiSub is running (PID {pid}) for this data folder. Stop it first (tray icon > Exit, "
              "or stop the OmbiSub task), then import again; it would otherwise keep writing its old "
              "state over the imported one. (--allow-running skips this check.)", file=sys.stderr)
        return 1

    # Parse path replacements (format: OLD=NEW)
    path_replacements = []
    if args.replace:
        for item in args.replace:
            if "=" in item:
                old_pfx, new_pfx = item.split("=", 1)
                path_replacements.append({"from": old_pfx, "to": new_pfx})
            else:
                print(f"[-] Warning: ignoring malformed replacement '{item}' (expected OLD=NEW)")

    print(f"[*] Inspecting and verifying bundle: {input_path}")
    try:
        inspect_migration_bundle(input_path)
    except Exception as e:
        print(f"[-] Import aborted: corrupt or invalid bundle ({e})", file=sys.stderr)
        return 1

    print(f"[*] Restoring migration bundle to {target_dir or 'OMNISUB_DATA_DIR'}...")
    if args.remap_unc_to_local:
        print("    UNC path remapping enabled (e.g. \\\\server\\e\\... -> E:\\...)")
    if path_replacements:
        for r in path_replacements:
            print(f"    Custom replacement: '{r['from']}' -> '{r['to']}'")

    try:
        summary = import_migration_bundle(
            zip_path=input_path,
            remap_unc_to_local=args.remap_unc_to_local,
            path_replacements=path_replacements,
            target_data_dir=target_dir,
        )
        print("[+] Import completed successfully!")
        print(f"    Projects restored : {summary.get('projects_restored')}")
        print(f"    Episodes restored : {summary.get('episodes_restored')}")
        print(f"    Paths remapped    : {summary.get('paths_remapped')}")
        print("    Restart required  : Please restart Omnisub service to reload databases.")
        return 0
    except Exception as e:
        print(f"[-] Error during bundle restore: {e}", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Omnisub Server Migration Tool (Export / Import / Inspect)"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # export
    p_export = subparsers.add_parser("export", help="Export server state to a migration bundle (.zip)")
    p_export.add_argument("--output", "-o", help="Destination path for the .zip bundle", default=None)
    p_export.add_argument(
        "--mode",
        choices=["compact", "full"],
        default="full",
        help="'full' (default, for moving the server) includes each episode's source subtitle; 'compact' leaves "
             "those out (a backup of settings and translations: ASS episodes cannot be re-exported from it)",
    )
    p_export.add_argument("--data-dir", help="Source OMNISUB_DATA_DIR (defaults to configured data dir)", default=None)
    p_export.set_defaults(func=cmd_export)

    # inspect
    p_inspect = subparsers.add_parser("inspect", help="Verify checksums and display bundle manifest")
    p_inspect.add_argument("--input", "-i", required=True, help="Path to migration bundle (.zip)")
    p_inspect.set_defaults(func=cmd_inspect)

    # import
    p_import = subparsers.add_parser("import", help="Import a migration bundle into this server")
    p_import.add_argument("--input", "-i", required=True, help="Path to migration bundle (.zip)")
    p_import.add_argument(
        "--remap-unc-to-local",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Automatically remap UNC drive shares (e.g. \\\\server\\e\\...) to local drives (E:\\...)",
    )
    p_import.add_argument(
        "--replace",
        action="append",
        help="Explicit prefix replacement in the format 'OLD=NEW' (can be specified multiple times)",
    )
    p_import.add_argument(
        "--target-dir",
        help="Target data directory (defaults to configured OMNISUB_DATA_DIR)",
        default=None,
    )
    p_import.add_argument(
        "--allow-running",
        action="store_true",
        help="Import even while an OmbiSub server is running for the target folder",
    )
    p_import.set_defaults(func=cmd_import)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
