"""
KRAB — Connector Routes (FIXED: injects OAuth creds from env vars)
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
import secrets
import logging
import os

from ..database import get_db
from ..dependencies import get_current_user
from ..models import ConnectorConfig, User, ConnectorStatus, SyncLog, KnowledgeChunk
from ..integrations import create_connector, get_all_connector_types
from ..integrations.connector_settings import get_oauth_creds
from ..connector_sync import ConnectorSyncService
from ..embedding import create_embedding

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/connectors", tags=["connectors"])


def _make_connector(connector_type: str, config: ConnectorConfig = None):
    """Create a connector instance with OAuth creds from env vars."""
    creds = get_oauth_creds(connector_type)
    return create_connector(
        connector_type,
        config=(config.config if config else {}) or {},
        access_token=config.access_token if config else None,
        refresh_token=config.refresh_token if config else None,
        client_id=creds["client_id"],
        client_secret=creds["client_secret"],
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
        creds = get_oauth_creds(t["type"])
        t["configured"] = bool(creds.get("client_id"))
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
    creds = get_oauth_creds(connector_type)
    if not creds.get("client_id"):
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
            from datetime import datetime
            config.token_expires_at = datetime.fromtimestamp(float(tokens["expires_at"]))
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
