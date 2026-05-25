"""
KRAB — Connector Sync Service
Orchestrates background syncing of connected data sources.
"""

from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import logging

from .models import ConnectorConfig, KnowledgeChunk, SyncLog, ConnectorStatus
from .integrations import create_connector, Document

logger = logging.getLogger(__name__)


class ConnectorSyncService:
    def __init__(self, db: Session, chunking_fn=None, embedding_fn=None):
        """
        Args:
            db: SQLAlchemy session
            chunking_fn: Function that takes text and returns list of chunk strings
            embedding_fn: Function that takes text and returns embedding vector
        """
        self.db = db
        self.chunk_text = chunking_fn
        self.embed_text = embedding_fn

    async def sync_connector(self, connector_config: ConnectorConfig) -> Dict[str, Any]:
        """
        Run a full or incremental sync for a connector.
        Returns sync stats.
        """
        connector_id = connector_config.id
        company_id = connector_config.company_id
        connector_type = connector_config.connector_type

        # Create sync log
        sync_log = SyncLog(
            connector_id=connector_id,
            company_id=company_id,
            status="started",
        )
        self.db.add(sync_log)
        connector_config.status = ConnectorStatus.SYNCING
        self.db.commit()

        try:
            # Instantiate connector
            connector = create_connector(
                connector_type,
                config=connector_config.config or {},
                access_token=connector_config.access_token,
                refresh_token=connector_config.refresh_token,
            )

            # Check if token needs refresh
            if connector_config.token_expires_at and connector_config.token_expires_at < datetime.utcnow():
                try:
                    new_tokens = await connector.refresh_access_token()
                    connector_config.access_token = new_tokens.get("access_token")
                    if new_tokens.get("refresh_token"):
                        connector_config.refresh_token = new_tokens["refresh_token"]
                    connector.access_token = connector_config.access_token
                    self.db.commit()
                except Exception as e:
                    logger.error(f"Token refresh failed for connector {connector_id}: {e}")
                    raise

            # Fetch documents (incremental if not first sync)
            since = connector_config.last_sync_at if connector_config.last_sync_at else None
            documents = await connector.fetch_documents(since=since)

            # Process documents
            added = 0
            updated = 0
            deleted = 0

            for doc in documents:
                result = self._process_document(doc, connector_config)
                if result == "added":
                    added += 1
                elif result == "updated":
                    updated += 1

            # Update connector status
            connector_config.status = ConnectorStatus.CONNECTED
            connector_config.last_sync_at = datetime.utcnow()
            connector_config.last_sync_status = "success"
            connector_config.last_sync_message = f"Synced {added} new, {updated} updated documents"
            connector_config.documents_indexed = (
                self.db.query(KnowledgeChunk)
                .filter_by(company_id=company_id, connector_id=connector_id)
                .count()
            )

            # Update sync log
            sync_log.status = "completed"
            sync_log.documents_added = added
            sync_log.documents_updated = updated
            sync_log.documents_deleted = deleted
            sync_log.completed_at = datetime.utcnow()

            self.db.commit()

            logger.info(
                f"Sync completed for {connector_type} (company={company_id}): "
                f"+{added} ~{updated} -{deleted}"
            )

            return {
                "success": True,
                "added": added,
                "updated": updated,
                "deleted": deleted,
                "total_indexed": connector_config.documents_indexed,
            }

        except Exception as e:
            logger.error(f"Sync failed for connector {connector_id}: {e}")

            self.db.rollback()

            connector_config.status = ConnectorStatus.ERROR
            connector_config.last_sync_status = "error"
            connector_config.last_sync_message = str(e)[:500]

            sync_log.status = "failed"
            sync_log.error_message = str(e)[:1000]
            sync_log.completed_at = datetime.utcnow()

            self.db.commit()

            return {"success": False, "error": str(e)}

    def _process_document(self, doc: Document, connector_config: ConnectorConfig) -> str:
        """
        Process a single document: chunk it, embed it, store in KB.
        Returns 'added' or 'updated'.
        """
        company_id = connector_config.company_id
        connector_id = connector_config.id
        source_app = connector_config.connector_type

        # Check if document already exists
        existing = (
            self.db.query(KnowledgeChunk)
            .filter_by(
                company_id=company_id,
                source_app=source_app,
                source_id=doc.external_id,
            )
            .all()
        )

        # If exists and not updated, skip
        if existing and doc.updated_at:
            latest_sync = max((c.last_synced_at for c in existing if c.last_synced_at), default=None)
            if latest_sync and doc.updated_at <= latest_sync:
                return "skipped"

        # Delete old chunks for this document (will re-create)
        if existing:
            for chunk in existing:
                self.db.delete(chunk)
            self.db.flush()

        # Chunk the content
        chunks = self._chunk_content(doc.content)

        # Create knowledge chunks
        for chunk_text in chunks:
            # Generate embedding
            embedding = None
            if self.embed_text:
                try:
                    embedding = self.embed_text(chunk_text)
                except Exception as e:
                    logger.warning(f"Embedding failed: {e}")

            chunk = KnowledgeChunk(
                company_id=company_id,
                text=chunk_text,
                embedding=embedding,
                source_type=doc.source_type,
                source_app=source_app,
                source_url=doc.source_url,
                source_id=doc.external_id,
                source_title=doc.title,
                connector_id=connector_id,
                metadata_=doc.metadata,
                last_synced_at=datetime.utcnow(),
            )
            self.db.add(chunk)

        return "updated" if existing else "added"

    def _chunk_content(self, text: str, chunk_size: int = 1000, overlap: int = 200) -> List[str]:
        """Split text into overlapping chunks."""
        if self.chunk_text:
            return self.chunk_text(text)

        # Default chunking: split by paragraphs first, then by size
        chunks = []
        paragraphs = text.split("\n\n")
        current_chunk = ""

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            if len(current_chunk) + len(para) <= chunk_size:
                current_chunk += ("\n\n" + para if current_chunk else para)
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                    # Keep overlap
                    overlap_text = current_chunk[-overlap:] if len(current_chunk) > overlap else ""
                    current_chunk = overlap_text + "\n\n" + para
                else:
                    # Single paragraph is larger than chunk_size - split by sentences
                    sentences = para.replace(". ", ".\n").split("\n")
                    for sent in sentences:
                        if len(current_chunk) + len(sent) <= chunk_size:
                            current_chunk += (" " + sent if current_chunk else sent)
                        else:
                            if current_chunk:
                                chunks.append(current_chunk)
                            current_chunk = sent

        if current_chunk.strip():
            chunks.append(current_chunk.strip())

        return chunks if chunks else [text[:chunk_size]]

    async def sync_all_due(self):
        """Find all connectors that are due for sync and run them."""
        now = datetime.utcnow()
        connectors = (
            self.db.query(ConnectorConfig)
            .filter(
                ConnectorConfig.status.in_([ConnectorStatus.CONNECTED, ConnectorStatus.ERROR]),
                ConnectorConfig.access_token != None,
            )
            .all()
        )

        synced = 0
        for config in connectors:
            # Check if due for sync
            if config.last_sync_at:
                next_sync = config.last_sync_at + timedelta(minutes=config.sync_frequency_minutes or 60)
                if now < next_sync:
                    continue

            logger.info(f"Starting sync for {config.connector_type} (company={config.company_id})")
            await self.sync_connector(config)
            synced += 1

        return synced

    def get_sync_history(self, connector_id: int, limit: int = 20) -> List[SyncLog]:
        return (
            self.db.query(SyncLog)
            .filter_by(connector_id=connector_id)
            .order_by(SyncLog.started_at.desc())
            .limit(limit)
            .all()
        )
