"""Backup and Migration Router for Omnisub API.

Endpoints:
  - GET  /api/backup/estimate: estimates bundle size and item counts
  - GET/POST /api/backup/export: streams a generated .zip migration bundle
  - POST /api/backup/inspect: validates uploaded bundle and returns manifest
  - POST /api/backup/import: restores bundle, pauses worker, updates metadata index
"""

import asyncio
import json
import logging
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from services.backup_service import (
    create_migration_bundle,
    get_backup_estimate,
    import_migration_bundle,
    inspect_migration_bundle,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/backup", tags=["backup"])


@router.get("/estimate")
async def estimate_backup():
    """Return project/episode counts and estimated backup sizes."""
    return await asyncio.to_thread(get_backup_estimate)


@router.api_route("/export", methods=["GET", "POST"])
async def export_backup(mode: str = Query("compact"), request: Request = None):
    """Build and stream migration .zip bundle (compact or full)."""
    if request and request.method == "POST":
        try:
            body = await request.json()
            if isinstance(body, dict) and "mode" in body:
                mode = body["mode"]
        except Exception:
            pass

    if mode not in ("compact", "full"):
        raise HTTPException(status_code=400, detail="mode must be 'compact' or 'full'")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"ombisub-migration-{mode}-{timestamp}.zip"

    bundle_path = await asyncio.to_thread(create_migration_bundle, mode=mode)

    return FileResponse(
        path=str(bundle_path),
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(lambda: bundle_path.unlink(missing_ok=True)),
    )


@router.post("/inspect")
async def inspect_backup(file: UploadFile = File(...)):
    """Inspect and verify checksums of an uploaded migration bundle."""
    suffix = Path(file.filename or "bundle.zip").suffix or ".zip"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = Path(tmp.name)
        shutil.copyfileobj(file.file, tmp)

    try:
        manifest_info = await asyncio.to_thread(inspect_migration_bundle, tmp_path)
        return manifest_info
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


@router.post("/import")
async def import_backup(
    file: UploadFile = File(...),
    remap_unc_to_local: Optional[bool] = Form(None),
    convert_unc_paths: Optional[bool] = Form(None),
    path_replacements_json: str = Form("[]"),
):
    """Restore a migration bundle onto this server."""
    do_remap = (
        remap_unc_to_local
        if remap_unc_to_local is not None
        else (convert_unc_paths if convert_unc_paths is not None else True)
    )
    try:
        path_replacements = json.loads(path_replacements_json) if path_replacements_json else []
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid path_replacements_json format")

    suffix = Path(file.filename or "bundle.zip").suffix or ".zip"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = Path(tmp.name)
        shutil.copyfileobj(file.file, tmp)

    try:
        # Pause background worker if running
        try:
            from services import background_worker
            worker = getattr(
                background_worker,
                "get_worker",
                lambda: getattr(background_worker, "worker", None),
            )()
            if worker and hasattr(worker, "pause"):
                worker.pause()
        except Exception:
            pass

        # Import migration bundle
        result = await asyncio.to_thread(
            import_migration_bundle,
            zip_path=tmp_path,
            remap_unc_to_local=do_remap,
            path_replacements=path_replacements,
        )

        # Reload storage configuration and state
        try:
            from utils import storage
            if hasattr(storage, "load_config_and_state"):
                storage.load_config_and_state()
        except Exception:
            pass

        # Trigger metadata_index backfill if available
        try:
            from utils import metadata_index
            if hasattr(metadata_index, "backfill_from_disk"):
                await asyncio.to_thread(metadata_index.backfill_from_disk)
            elif hasattr(metadata_index, "backfill"):
                await asyncio.to_thread(metadata_index.backfill)
        except Exception:
            pass

        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
