"""Knowledge routes: upload text, upload files, list documents."""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import get_db
from app import models
from app.dependencies import get_current_user
from app.embedding import create_embedding
from app.chunking import chunk_text
from app.models import KnowledgeChunk, User
from app.file_parser import parse_uploaded_file, parse_raw_text

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.post("/upload")
def upload_knowledge(
    data: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Upload raw text as knowledge."""
    content = data.get("content")
    if not content or not content.strip():
        raise HTTPException(status_code=400, detail="No content provided")

    parsed = parse_raw_text(content, source_name=data.get("source_name", "pasted_text"))
    chunks = chunk_text(parsed.text)

    if not chunks:
        raise HTTPException(status_code=400, detail="Content too short to process")

    chunks_created = 0
    for chunk in chunks:
        embedding = create_embedding(chunk)
        item = KnowledgeChunk(
            company_id=user.company_id,
            user_id=user.id,
            source_type=data.get("source_type", "document"),
            source_id=data.get("source_id", "0"),
            text=chunk,
            embedding=embedding
        )
        db.add(item)
        chunks_created += 1

    db.commit()
    return {"message": "Knowledge stored", "chunks": chunks_created}


@router.post("/upload-file")
async def upload_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Upload a PDF, DOCX, TXT, CSV, or MD file as knowledge."""
    parsed = await parse_uploaded_file(file)
    if not parsed.is_valid:
        raise HTTPException(status_code=400, detail=parsed.error)

    chunks = chunk_text(parsed.text)
    if not chunks:
        raise HTTPException(status_code=400, detail="No content extracted from file.")

    chunks_created = 0
    for chunk in chunks:
        embedding = create_embedding(chunk)
        item = KnowledgeChunk(
            company_id=user.company_id,
            user_id=user.id,
            source_type=f"file:{parsed.file_type}",
            source_id="0",
            text=chunk,
            embedding=embedding
        )
        db.add(item)
        chunks_created += 1

    db.commit()

    return {
        "success": True,
        "filename": parsed.filename,
        "file_type": parsed.file_type,
        "pages": parsed.page_count,
        "words_extracted": parsed.word_count,
        "chunks_created": chunks_created
    }


@router.get("/documents")
def list_documents(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Return actual stored chunks (acts like file listing for now)."""
    docs = db.execute(
        text("""
            SELECT id, source_type, text, created_at
            FROM knowledge_chunks
            WHERE user_id = :user_id
            ORDER BY created_at DESC
            LIMIT 100
        """),
        {"user_id": user.id}
    ).mappings().all()

    return {"documents": [dict(d) for d in docs], "total": len(docs)}


@router.delete("/source/{source_type}")
def delete_knowledge_source(
    source_type: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Delete all knowledge chunks belonging to a given source_type for this user."""
    deleted = db.query(KnowledgeChunk).filter(
        KnowledgeChunk.user_id == user.id,
        KnowledgeChunk.source_type == source_type
    ).delete(synchronize_session=False)
    db.commit()

    if deleted == 0:
        raise HTTPException(status_code=404, detail="No chunks found for that source type.")

    return {"deleted": deleted, "source_type": source_type}


@router.delete("/chunk/{chunk_id}")
def delete_knowledge_chunk(
    chunk_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Delete a single knowledge chunk by ID."""
    chunk = db.query(KnowledgeChunk).filter(
        KnowledgeChunk.id == chunk_id,
        KnowledgeChunk.user_id == user.id
    ).first()

    if not chunk:
        raise HTTPException(status_code=404, detail="Chunk not found.")

    db.delete(chunk)
    db.commit()

    return {"deleted": True, "chunk_id": chunk_id}