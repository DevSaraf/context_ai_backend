"""
ingestion.py
============
The bridge that was missing: turns a connector's `Document`s into the SAME
`knowledge_chunks` your RAG + synthesis pipeline already uses. Every connector
flows through this one path, so the rest of the system never knows or cares
where a document came from.

Guarantees:
  * Dedup by (integration, external_id): re-syncing the same doc does nothing.
  * Change detection by content hash: an edited doc has its OLD chunks deleted
    and new ones written — no stale duplicates.
  * Provenance: a document's chunks all carry source_id == synced_documents.id
    and source_type == "<connector>:<subtype>", so they're traceable and
    replaceable as a unit.

Reuses the existing KRAB modules (chunking, embedding, models.KnowledgeChunk).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

# Existing KRAB pipeline pieces.
from .chunking import chunk_text  # type: ignore
from .embedding import create_embedding  # type: ignore
from .models import KnowledgeChunk  # type: ignore

from .connector_models import ConnectorIntegration, SyncedDocument
from .connectors.base_connector import Document

logger = logging.getLogger("krab.ingestion")


@dataclass
class IngestResult:
    fetched: int = 0
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    chunks_created: int = 0
    chunks_deleted: int = 0

    @property
    def changed(self) -> int:
        return self.new + self.updated


def ingest_documents(
    db: Session,
    *,
    integration: ConnectorIntegration,
    documents: List[Document],
    chunk_size: int = 400,
    overlap: int = 80,
    min_chunk_size: int = 50,
) -> IngestResult:
    """Ingest a batch of documents for one integration. Idempotent per doc."""
    result = IngestResult(fetched=len(documents))
    company_id = integration.company_id
    user_id = integration.user_id
    ctype = integration.connector_type

    for doc in documents:
        if not doc.is_meaningful():
            continue
        try:
            _ingest_one(
                db,
                integration=integration,
                doc=doc,
                result=result,
                chunk_size=chunk_size,
                overlap=overlap,
                min_chunk_size=min_chunk_size,
            )
            db.commit()
        except Exception as exc:  # one bad doc must not abort the whole sync
            db.rollback()
            logger.warning("ingest failed for %s:%s — %s", ctype, doc.external_id, exc)

    logger.info(
        "ingest[%s/%s]: new=%d updated=%d unchanged=%d chunks+=%d chunks-=%d",
        company_id, ctype, result.new, result.updated, result.unchanged,
        result.chunks_created, result.chunks_deleted,
    )
    return result


def _ingest_one(
    db: Session,
    *,
    integration: ConnectorIntegration,
    doc: Document,
    result: IngestResult,
    chunk_size: int,
    overlap: int,
    min_chunk_size: int,
) -> None:
    chash = doc.content_hash()
    existing: SyncedDocument | None = (
        db.query(SyncedDocument)
        .filter(
            SyncedDocument.integration_id == integration.id,
            SyncedDocument.external_id == doc.external_id,
        )
        .first()
    )

    if existing and existing.content_hash == chash:
        result.unchanged += 1
        return  # nothing changed

    source_type = f"{integration.connector_type}:{doc.source_type or 'doc'}"

    if existing:
        # Changed: drop the old chunks for this exact document, then rebuild.
        deleted = _delete_chunks_for(db, integration.company_id, existing.id, integration.connector_type)
        result.chunks_deleted += deleted
        synced = existing
        synced.content_hash = chash
        synced.title = doc.title
        synced.source_url = doc.source_url
        synced.updated_at = doc.updated_at
        synced.synced_at = datetime.now(timezone.utc)
        result.updated += 1
    else:
        synced = SyncedDocument(
            integration_id=integration.id,
            company_id=integration.company_id,
            connector_type=integration.connector_type,
            external_id=doc.external_id,
            content_hash=chash,
            title=doc.title,
            source_url=doc.source_url,
            updated_at=doc.updated_at,
        )
        db.add(synced)
        db.flush()  # assign synced.id (used as chunk source_id)
        result.new += 1

    # Prepend the title so a chunk carries context even in isolation.
    body = f"{doc.title}\n\n{doc.content}" if doc.title else doc.content
    chunks = chunk_text(
        body, chunk_size=chunk_size, overlap=overlap, min_chunk_size=min_chunk_size
    )

    created = 0
    for chunk in chunks:
        if not chunk or not chunk.strip():
            continue
        embedding = create_embedding(chunk)
        db.add(
            KnowledgeChunk(
                company_id=integration.company_id,
                user_id=integration.user_id,
                source_type=source_type,   # e.g. "notion:wiki"
                source_id=str(synced.id),  # existing chunk model expects str
                text=chunk,
                embedding=embedding,
            )
        )
        created += 1

    synced.chunk_count = created
    result.chunks_created += created


def _delete_chunks_for(db: Session, company_id: str, synced_id: int, connector_type: str) -> int:
    """Delete all knowledge_chunks produced by one synced document."""
    res = db.execute(
        sql_text(
            """
            DELETE FROM knowledge_chunks
            WHERE company_id = :cid
              AND source_id = :sid
              AND source_type LIKE :prefix
            """
        ),
        {"cid": company_id, "sid": str(synced_id), "prefix": f"{connector_type}:%"},
    )
    return res.rowcount or 0
