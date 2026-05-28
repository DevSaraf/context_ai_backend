import os

ROUTER_PATH = r"D:\context-ai-backend\app\routers\connector_router.py"

with open(ROUTER_PATH, "r", encoding="utf-8") as f:
    content = f.read()

# Add imports
if "from pydantic import BaseModel" not in content:
    content = content.replace("from fastapi import APIRouter", "from pydantic import BaseModel\nfrom typing import List\nfrom fastapi import APIRouter")

if "BackgroundTasks" not in content:
    content = content.replace("from fastapi import APIRouter, Depends", "from fastapi import APIRouter, Depends, BackgroundTasks")

if "SessionLocal" not in content:
    content = content.replace("from ..database import get_db", "from ..database import get_db, SessionLocal")

if "from .. import crypto" not in content:
    content = content.replace("from ..oauth_credentials import credential_provider", "from ..oauth_credentials import credential_provider\nfrom .. import crypto")

routes = """

# ============================================================
# PICKER ENDPOINTS
# ============================================================

# --------------------------------------------------------------------------- #
# 1. Picker config — everything the browser needs to open the Picker
# --------------------------------------------------------------------------- #
@router.get("/google_drive/picker-config")
async def picker_config(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    cfg = db.query(ConnectorConfig).filter_by(company_id=user.company_id, connector_type="google_drive").first()
    if not cfg:
        raise HTTPException(status_code=404, detail="Connector not connected.")

    # Decrypt the stored access token and refresh it if needed, so the Picker
    # always opens with a live token. We reuse the connector's refresh logic.
    access = crypto.decrypt(cfg.access_token or "")
    refresh = crypto.decrypt(cfg.refresh_token or "")
    creds = credential_provider.get("google_drive")

    from ..integrations.google_drive import GoogleDriveConnector
    connector = GoogleDriveConnector(
        config={}, access_token=access, refresh_token=refresh,
        client_id=creds.client_id, client_secret=creds.client_secret,
    )

    # Cheap liveness check; refresh on failure.
    ok, _ = await connector.test_connection()
    if not ok and refresh:
        try:
            new = await connector.refresh_access_token()
            access = new["access_token"]
            cfg.access_token = crypto.encrypt(access)
            if new.get("refresh_token"):
                cfg.refresh_token = crypto.encrypt(new["refresh_token"])
            cfg.token_expires_at = new.get("expires_at")
            db.commit()
        except Exception as e:
            logger.warning("Picker token refresh failed: %s", e)
            raise HTTPException(status_code=401, detail="Google session expired. Please reconnect Google Drive.")

    api_key = os.environ.get("GOOGLE_PICKER_API_KEY", "")
    app_id = os.environ.get("GOOGLE_PROJECT_NUMBER", "")  # numeric project id
    if not api_key or not app_id:
        raise HTTPException(
            status_code=503,
            detail="Picker not configured on this deployment (missing GOOGLE_PICKER_API_KEY / GOOGLE_PROJECT_NUMBER).",
        )

    # Return the IDs already selected so the UI can show current state.
    selected = (cfg.config or {}).get("selected_file_ids", []) if hasattr(cfg, "config") else []

    return {
        "oauth_token": access,
        "api_key": api_key,
        "app_id": app_id,
        "selected_file_ids": selected,
    }


# --------------------------------------------------------------------------- #
# 2. Save the user's selection + trigger a sync of just those files
# --------------------------------------------------------------------------- #
class SelectionBody(BaseModel):
    file_ids: List[str]


@router.post("/google_drive/selection")
def save_selection(
    body: SelectionBody,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    cfg = db.query(ConnectorConfig).filter_by(company_id=user.company_id, connector_type="google_drive").first()
    if not cfg:
        raise HTTPException(status_code=404, detail="Connector not connected.")

    # Persist on the integration. We store inside the JSON `config` column.
    config_dict = dict(cfg.config or {}) if hasattr(cfg, "config") else {}
    config_dict["selected_file_ids"] = list(dict.fromkeys(body.file_ids))  # dedupe, keep order
    cfg.config = config_dict
    db.commit()

    # Kick off a background sync of the selected files (fresh session inside).
    background.add_task(_sync_selected, cfg.id)
    return {"saved": True, "count": len(config_dict["selected_file_ids"])}


def _sync_selected(config_id: int):
    \"\"\"Background worker — opens its OWN session, never reuses the request's.\"\"\"
    db = SessionLocal()
    try:
        cfg = db.query(ConnectorConfig).get(config_id)
        if not cfg:
            return
        
        from ..connector_sync import ConnectorSyncService
        from ..embedding import create_embedding
        sync_service = ConnectorSyncService(db, embedding_fn=create_embedding)
        
        import asyncio
        asyncio.run(sync_service.sync_connector(cfg))
    except Exception:
        logger.exception("Selected-file sync failed for config %s", config_id)
    finally:
        db.close()
"""

if "picker_config" not in content:
    content += routes

with open(ROUTER_PATH, "w", encoding="utf-8") as f:
    f.write(content)
print("Updated connector_router.py")
