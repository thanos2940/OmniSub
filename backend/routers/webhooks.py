"""
Webhooks API Router
===================
Handles incoming external webhooks, including GitHub push events for instant auto-updates.
"""

import hmac
import hashlib
import logging
from typing import Optional
from fastapi import APIRouter, Request, Header, HTTPException

from services.update_service import update_service
from utils import storage

logger = logging.getLogger("omnisub.webhooks")
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


def _verify_github_signature(raw_body: bytes, signature_header: Optional[str], secret: str) -> bool:
    """Verify GitHub HMAC-SHA256 signature. Without a configured secret nothing verifies: a push event makes
    the server update and restart, which nobody who merely reaches the port may trigger."""
    if not secret:
        return False
    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected_sig = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(expected_sig, signature_header)


@router.post("/github")
async def github_webhook(
    request: Request,
    x_github_event: Optional[str] = Header(None, alias="X-GitHub-Event"),
    x_hub_signature_256: Optional[str] = Header(None, alias="X-Hub-Signature-256"),
):
    """Receive GitHub push events and install the update: at once when nothing is in progress, else as soon as
    the work in hand allows (services/update_service.request_update_when_idle). The reply says which."""
    config = storage.load_global_config()
    webhook_enabled = config.get("github_webhook_enabled", True)
    auto_update_enabled = config.get("auto_update_enabled", True)
    secret = config.get("github_webhook_secret", "")

    raw_body = await request.body()

    # 1. Verify the HMAC signature; without a secret the hook is off (it can restart the server)
    if not secret:
        logger.warning("GitHub webhook rejected: no github_webhook_secret is configured.")
        raise HTTPException(status_code=401, detail="Set github_webhook_secret before using the GitHub webhook.")
    if not _verify_github_signature(raw_body, x_hub_signature_256, secret):
        logger.warning("GitHub webhook rejected: invalid HMAC signature.")
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")

    # 2. Handle Ping event
    if x_github_event == "ping":
        logger.info("GitHub webhook 'ping' event received and verified.")
        return {"status": "pong", "message": "GitHub webhook verified successfully."}

    # 3. Handle Push event
    if x_github_event == "push":
        if not webhook_enabled:
            logger.info("GitHub webhook push event ignored: github_webhook_enabled is False.")
            return {"status": "ignored", "message": "GitHub webhooks are disabled in settings."}

        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON payload.")

        ref = payload.get("ref", "")
        branch = ref.replace("refs/heads/", "")
        head_commit = payload.get("head_commit", {})
        commit_id = (head_commit.get("id") or "")[:7]
        commit_msg = head_commit.get("message", "").split("\n")[0]
        sender = payload.get("sender", {}).get("login", "unknown")

        logger.info(
            f"GitHub push received: branch '{branch}', commit '{commit_id}' by '{sender}': {commit_msg}"
        )

        # Only update if push is to main
        if branch not in ("main", "master"):
            return {"status": "ignored", "message": f"Push was to '{branch}', not main branch."}

        if not auto_update_enabled:
            logger.info("Auto-update is disabled in settings; update not applied automatically.")
            update_service.note_remote_change()   # the UI shows the new commits without waiting for the next check
            return {"status": "received", "message": "Push received. Auto-update is disabled."}

        source = f"GitHub push {commit_id}" if commit_id else "GitHub push"
        answer = update_service.request_update_when_idle(source=source, commit=commit_id)
        if answer["status"] == "deferred":
            logger.info(f"GitHub push {commit_id}: work is in progress, the update waits. {answer['message']}")
        return {"status": answer["status"], "message": answer["message"], "busy": answer["busy"]}

    return {"status": "unhandled", "message": f"Unhandled event type: {x_github_event}"}
