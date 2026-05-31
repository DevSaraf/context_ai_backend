"""
sync_service.py
===============
Scheduler entry point: finds all ConnectorConfig rows due for sync and runs them.
Delegates the actual sync work to ConnectorSyncService (connector_sync.py), which
owns the live implementation. All sync logic lives in one place.

Wire to a scheduler:
    from app.database import SessionLocal
    from app.sync_service import sync_all_due
    import asyncio
    asyncio.run(sync_all_due(SessionLocal()))
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List

from sqlalchemy.orm import Session

from app.models import ConnectorConfig, ConnectorStatus

logger = logging.getLogger("krab.sync")


async def sync_all_due(db: Session) -> List[int]:
    """Sync every connected integration whose interval has elapsed."""
    from app.connector_sync import ConnectorSyncService
    from app.embedding import create_embedding

    now = datetime.now(timezone.utc)
    due: List[ConnectorConfig] = []
    for cfg in (
        db.query(ConnectorConfig)
        .filter(ConnectorConfig.status.in_([ConnectorStatus.CONNECTED, ConnectorStatus.ERROR]))
        .filter(ConnectorConfig.access_token.isnot(None))
        .all()
    ):
        if cfg.last_sync_at is None:
            due.append(cfg)
            continue
        last = cfg.last_sync_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if now - last >= timedelta(minutes=cfg.sync_frequency_minutes or 60):
            due.append(cfg)

    svc = ConnectorSyncService(db, embedding_fn=create_embedding)
    synced_ids: List[int] = []
    for cfg in due:
        await svc.sync_connector(cfg)
        synced_ids.append(cfg.id)

    logger.info("sync_all_due: synced %d connectors", len(synced_ids))
    return synced_ids


async def run_sync(db: Session, config_id: int) -> dict:
    """Run a single sync by ConnectorConfig ID. Useful for manual triggers."""
    from app.connector_sync import ConnectorSyncService
    from app.embedding import create_embedding

    cfg = db.query(ConnectorConfig).get(config_id)
    if not cfg:
        raise ValueError(f"ConnectorConfig {config_id} not found")

    svc = ConnectorSyncService(db, embedding_fn=create_embedding)
    return await svc.sync_connector(cfg)
