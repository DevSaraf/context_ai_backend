"""
sync_service.py
===============
The orchestrator that runs a connector end to end and CLOSES THE LOOP that
makes KRAB a *living* company brain rather than a one-time import:

    connector.fetch_documents(since=watermark)
        -> ingestion.ingest_documents()        (-> knowledge_chunks)
        -> synthesis.run_synthesis()           (-> updated procedures/skills)

A connector never does any of this itself — it just yields Documents. All the
DB writes, token refresh, watermarking, dedup, and re-synthesis live here, in
ONE place, for EVERY connector. That's what makes adding a new source cheap and
keeps the whole thing consistent.

Also provides `sync_all_due()` — the entrypoint a scheduler (cron / APScheduler
/ Celery beat) calls periodically to keep every integration fresh.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.connector_models import (
    ConnectorIntegration,
    ConnectorSyncRun,
    IntegrationStatus,
    SyncRunStatus,
)
from app.connectors.base_connector import BaseConnector, get_connector_class
from app.crypto import decrypt, encrypt
from app.ingestion import ingest_documents

logger = logging.getLogger("krab.sync")

# Refresh a token if it expires within this window.
TOKEN_REFRESH_BUFFER = timedelta(minutes=5)


def build_connector(integration: ConnectorIntegration) -> BaseConnector:
    """Instantiate the registered connector class with decrypted tokens and a
    runtime-merged config (OAuth client creds injected from env, never stored).
    """
    cls = get_connector_class(integration.connector_type)
    config = dict(integration.config or {})
    config.update(_env_client_creds(integration.connector_type))
    return cls(
        config=config,
        access_token=decrypt(integration.access_token_enc),
        refresh_token=decrypt(integration.refresh_token_enc),
    )


def _env_client_creds(connector_type: str) -> Dict[str, str]:
    """Read OAuth client creds from env: e.g. NOTION_CLIENT_ID / _SECRET.

    Keeping secrets in env (not the DB) is deliberate — the `config` column is
    only for non-secret settings.
    """
    import os

    prefix = connector_type.upper()
    return {
        "client_id": os.getenv(f"{prefix}_CLIENT_ID", ""),
        "client_secret": os.getenv(f"{prefix}_CLIENT_SECRET", ""),
    }


async def run_sync(
    db: Session,
    integration: ConnectorIntegration,
    *,
    trigger_resynthesis: bool = True,
    full_refresh: bool = False,
) -> ConnectorSyncRun:
    """Run one sync for one integration. Safe to call repeatedly."""
    run = ConnectorSyncRun(
        integration_id=integration.id,
        company_id=integration.company_id,
        status=SyncRunStatus.running,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        connector = build_connector(integration)

        # 1) refresh token if near expiry
        await _maybe_refresh_token(db, integration, connector)

        # 2) verify the connection before doing real work
        ok, msg = await connector.test_connection()
        if not ok:
            raise RuntimeError(f"connection check failed: {msg}")

        # 3) fetch incrementally from the watermark (unless full refresh)
        since: Optional[datetime] = None if full_refresh else integration.last_sync_at
        sync_started = datetime.now(timezone.utc)
        documents = await connector.fetch_documents(since=since)

        # 4) ingest into knowledge_chunks (dedup + change detection)
        ingest = ingest_documents(db, integration=integration, documents=documents)
        run.documents_fetched = ingest.fetched
        run.documents_new = ingest.new
        run.documents_updated = ingest.updated
        run.documents_unchanged = ingest.unchanged
        run.chunks_created = ingest.chunks_created
        run.chunks_deleted = ingest.chunks_deleted

        # 5) advance the watermark + status
        integration.last_sync_at = sync_started
        integration.last_sync_status = "ok"
        integration.last_error = None
        integration.status = IntegrationStatus.active
        db.commit()

        # 6) keep the brain current: re-synthesize only if anything changed
        if trigger_resynthesis and ingest.changed > 0:
            try:
                from app.synthesis import run_synthesis  # local import avoids cycle

                await run_synthesis(
                    db,
                    company_id=integration.company_id,
                    user_id=integration.user_id,
                )
                run.triggered_resynthesis = True
            except Exception as exc:  # re-synthesis failure shouldn't fail the sync
                logger.exception("re-synthesis after sync failed: %s", exc)

        run.status = SyncRunStatus.completed
    except Exception as exc:
        logger.exception("sync failed for integration %s", integration.id)
        run.status = SyncRunStatus.failed
        run.error = str(exc)[:2000]
        integration.last_sync_status = "error"
        integration.last_error = str(exc)[:2000]
        integration.status = IntegrationStatus.error
    finally:
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(run)
    return run


async def _maybe_refresh_token(
    db: Session, integration: ConnectorIntegration, connector: BaseConnector
) -> None:
    if not integration.expires_at:
        return  # no expiry (e.g. Notion/Slack bot tokens)
    now = datetime.now(timezone.utc)
    expires = integration.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires - now > TOKEN_REFRESH_BUFFER:
        return  # still valid

    logger.info("refreshing token for integration %s", integration.id)
    data = await connector.refresh_access_token()
    if data.get("access_token"):
        integration.access_token_enc = encrypt(data["access_token"])
        connector.access_token = data["access_token"]
    if data.get("refresh_token"):
        integration.refresh_token_enc = encrypt(data["refresh_token"])
        connector.refresh_token = data["refresh_token"]
    if data.get("expires_at"):
        integration.expires_at = data["expires_at"]
    db.commit()


async def sync_all_due(db: Session) -> List[int]:
    """Sync every active integration whose interval has elapsed.

    Wire this to your scheduler:
        # APScheduler / Celery beat / cron calling a small script
        from database import SessionLocal
        from sync_service import sync_all_due
        import asyncio
        asyncio.run(sync_all_due(SessionLocal()))
    """
    now = datetime.now(timezone.utc)
    due: List[ConnectorIntegration] = []
    for integ in (
        db.query(ConnectorIntegration)
        .filter(ConnectorIntegration.status == IntegrationStatus.active)
        .all()
    ):
        if integ.last_sync_at is None:
            due.append(integ)
            continue
        last = integ.last_sync_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if now - last >= timedelta(minutes=integ.sync_interval_minutes):
            due.append(integ)

    synced_ids: List[int] = []
    for integ in due:
        await run_sync(db, integ)
        synced_ids.append(integ.id)
    logger.info("sync_all_due: synced %d integrations", len(synced_ids))
    return synced_ids
