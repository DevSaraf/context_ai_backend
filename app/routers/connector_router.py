"""
KRAB — Connector Routes (FIXED: injects OAuth creds from env vars)
"""

from pydantic import BaseModel
from typing import List
from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
import secrets
import logging
import os

from ..database import get_db, SessionLocal
from ..dependencies import get_current_user
from ..models import ConnectorConfig, User, ConnectorStatus, SyncLog, KnowledgeChunk
from ..integrations import create_connector, get_all_connector_types
from ..oauth_credentials import credential_provider
from .. import crypto
from ..connector_sync import ConnectorSyncService
from ..embedding import create_embedding

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/connectors", tags=["connectors"])


def _make_connector(connector_type: str, config: ConnectorConfig = None):
    """Create a connector instance with OAuth creds from env vars."""
    creds = credential_provider.get(connector_type)
    return create_connector(
        connector_type,
        config=(config.config if config else {}) or {},
        access_token=config.access_token if config else None,
        refresh_token=config.refresh_token if config else None,
        client_id=creds.client_id,
        client_secret=creds.client_secret,
    )


# ============================================================
# LIST ALL CONNECTORS
# ============================================================

@router.get("/")
async def list_connectors(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    configs = (
        db.query(ConnectorConfig)
        .filter_by(company_id=user.company_id)
        .order_by(ConnectorConfig.created_at)
        .all()
    )
    return {
        "connectors": [
            {
                "id": c.id,
                "connector_type": c.connector_type,
                "display_name": c.display_name,
                "status": c.status,
                "documents_indexed": c.documents_indexed or 0,
                "last_sync_at": c.last_sync_at.isoformat() if c.last_sync_at else None,
                "last_sync_status": c.last_sync_status,
                "last_sync_message": c.last_sync_message,
                "sync_frequency_minutes": c.sync_frequency_minutes or 60,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in configs
        ]
    }


@router.get("/types")
async def list_connector_types():
    """List all connector types and whether they have credentials configured."""
    types = get_all_connector_types()
    for t in types:
        creds = credential_provider.get(t["type"])
        t["configured"] = creds.is_configured
    return {"connectors": types}


# ============================================================
# SYNC LOGS
# ============================================================

@router.get("/sync-logs")
async def get_all_sync_logs(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    connector_ids = [
        c.id for c in
        db.query(ConnectorConfig.id).filter_by(company_id=user.company_id).all()
    ]
    if not connector_ids:
        return {"logs": []}

    logs = (
        db.query(SyncLog)
        .filter(SyncLog.connector_id.in_(connector_ids))
        .order_by(SyncLog.started_at.desc())
        .limit(50)
        .all()
    )
    configs = db.query(ConnectorConfig).filter(ConnectorConfig.id.in_(connector_ids)).all()
    type_map = {c.id: c.connector_type for c in configs}

    return {
        "logs": [
            {
                "id": l.id,
                "connector_type": type_map.get(l.connector_id, "unknown"),
                "status": l.status,
                "documents_added": l.documents_added or 0,
                "documents_updated": l.documents_updated or 0,
                "error_message": l.error_message,
                "started_at": l.started_at.isoformat() if l.started_at else None,
                "completed_at": l.completed_at.isoformat() if l.completed_at else None,
            }
            for l in logs
        ]
    }


# ============================================================
# HELPER: get or create connector config
# ============================================================

def _get_or_create_config(db: Session, user: User, connector_type: str) -> ConnectorConfig:
    config = db.query(ConnectorConfig).filter_by(
        company_id=user.company_id,
        connector_type=connector_type,
    ).first()

    if not config:
        config = ConnectorConfig(
            company_id=user.company_id,
            connector_type=connector_type,
            display_name=connector_type.replace("_", " ").title(),
            status=ConnectorStatus.DISCONNECTED,
            created_by=user.id,
        )
        db.add(config)
        db.commit()
        db.refresh(config)

    return config


# ============================================================
# OAUTH START — dashboard calls GET /connectors/by-type/{type}/oauth-url
# ============================================================

@router.get("/by-type/{connector_type}/oauth-url")
async def get_oauth_url(
    connector_type: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Check if OAuth credentials are configured
    creds = credential_provider.get(connector_type)
    if not creds.is_configured:
        return {
            "url": None,
            "error": f"OAuth not configured for {connector_type}. Set {connector_type.upper().replace('_','')}_CLIENT_ID and _CLIENT_SECRET in your .env file."
        }

    config = _get_or_create_config(db, user, connector_type)

    try:
        connector = _make_connector(connector_type, config)

        state = secrets.token_urlsafe(32)
        config.config = {**(config.config or {}), "_oauth_state": state}
        db.commit()

        base_url = str(request.base_url).rstrip("/")
        redirect_uri = f"{base_url}/connectors/oauth/callback"
        oauth_url = connector.get_oauth_url(redirect_uri, state)

        logger.info(f"OAuth URL generated for {connector_type}: {oauth_url[:80]}...")
        return {"url": oauth_url, "state": state}
    except Exception as e:
        logger.error(f"OAuth URL generation failed for {connector_type}: {e}")
        return {"url": None, "error": str(e)}


# ============================================================
# OAUTH CALLBACK
# ============================================================

@router.get("/oauth/callback")
async def oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
    request: Request = None,
    db: Session = Depends(get_db),
):
    # Find connector by state
    configs = db.query(ConnectorConfig).all()
    config = None
    for c in configs:
        if c.config and c.config.get("_oauth_state") == state:
            config = c
            break

    if not config:
        raise HTTPException(400, "Invalid OAuth state — session may have expired. Please try connecting again.")

    try:
        connector = _make_connector(config.connector_type, config)

        base_url = str(request.base_url).rstrip("/")
        redirect_uri = f"{base_url}/connectors/oauth/callback"

        tokens = await connector.exchange_code(code, redirect_uri)

        config.access_token = tokens["access_token"]
        config.refresh_token = tokens.get("refresh_token", "")
        if tokens.get("expires_at"):
            exp = tokens["expires_at"]
            if isinstance(exp, (int, float)):
                from datetime import datetime, timezone
                exp = datetime.fromtimestamp(exp, tz=timezone.utc)
            elif isinstance(exp, str) and exp.isdigit():
                from datetime import datetime, timezone
                exp = datetime.fromtimestamp(float(exp), tz=timezone.utc)
            config.token_expires_at = exp
        config.status = ConnectorStatus.CONNECTED

        # Store extra config (workspace_id, cloud_id, team_name, etc.)
        extra = {k: v for k, v in tokens.items() if k not in ("access_token", "refresh_token", "expires_at")}
        if extra:
            config.config = {**(config.config or {}), **extra}

        # Clean up state
        if config.config:
            config.config.pop("_oauth_state", None)

        db.commit()

        logger.info(f"OAuth callback success for {config.connector_type}")

        # Redirect back to dashboard connectors page
        dashboard_url = base_url + "/dashboard.html"
        return RedirectResponse(url=dashboard_url, status_code=302)

    except Exception as e:
        logger.error(f"OAuth callback error for {config.connector_type}: {e}")
        config.status = ConnectorStatus.ERROR
        config.last_sync_message = str(e)[:500]
        db.commit()
        raise HTTPException(500, f"OAuth failed: {str(e)}")


# ============================================================
# SYNC — dashboard calls POST /connectors/by-type/{type}/sync
# ============================================================

@router.post("/by-type/{connector_type}/sync")
async def sync_by_type(
    connector_type: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    config = db.query(ConnectorConfig).filter_by(
        company_id=user.company_id,
        connector_type=connector_type,
    ).first()

    if not config:
        raise HTTPException(404, f"Connector {connector_type} not found")
    if not config.access_token:
        raise HTTPException(400, "Connector not authenticated. Complete OAuth first.")
    if config.status == ConnectorStatus.SYNCING:
        raise HTTPException(400, "Sync already in progress")

    sync_service = ConnectorSyncService(db, embedding_fn=create_embedding)
    result = await sync_service.sync_connector(config)
    return result


# ============================================================
# DISCONNECT — dashboard calls DELETE /connectors/by-type/{type}
# ============================================================

@router.delete("/by-type/{connector_type}")
async def disconnect_by_type(
    connector_type: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    config = db.query(ConnectorConfig).filter_by(
        company_id=user.company_id,
        connector_type=connector_type,
    ).first()

    if not config:
        raise HTTPException(404, f"Connector {connector_type} not found")

    db.query(KnowledgeChunk).filter_by(connector_id=config.id).delete()
    db.query(SyncLog).filter_by(connector_id=config.id).delete()
    db.delete(config)
    db.commit()

    return {"success": True, "message": f"{connector_type} disconnected"}


# ============================================================
# TEST CONNECTION
# ============================================================

@router.post("/by-type/{connector_type}/test")
async def test_connection(
    connector_type: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    config = db.query(ConnectorConfig).filter_by(
        company_id=user.company_id,
        connector_type=connector_type,
    ).first()

    if not config or not config.access_token:
        raise HTTPException(400, "Connector not authenticated")

    connector = _make_connector(connector_type, config)
    success, message = await connector.test_connection()
    return {"success": success, "message": message}


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
    
    # MERGE with existing selection, don't replace.
    existing_ids = config_dict.get("selected_file_ids", []) or []
    merged = list(dict.fromkeys([*existing_ids, *body.file_ids]))  # union, preserve order
    config_dict["selected_file_ids"] = merged
    
    cfg.config = config_dict
    flag_modified(cfg, "config")
    db.commit()

    # Kick off a background sync of the selected files (fresh session inside).
    background.add_task(_sync_selected, cfg.id)
    return {"saved": True, "count": len(merged), "added_now": len(body.file_ids)}


def _sync_selected(config_id: int):
    """Background worker — opens its OWN session, never reuses the request's."""
    db = SessionLocal()
    try:
        cfg = db.query(ConnectorConfig).get(config_id)
        if not cfg:
            return
        
        from ..connector_sync import ConnectorSyncService
        from ..embedding import create_embedding
        sync_service = ConnectorSyncService(db, embedding_fn=create_embedding)
        
        import asyncio
        asyncio.run(sync_service.sync_connector(cfg, force_full=True))
    except Exception:
        logger.exception("Selected-file sync failed for config %s", config_id)
    finally:
        db.close()
