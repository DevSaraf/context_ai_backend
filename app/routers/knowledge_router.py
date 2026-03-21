"""Knowledge routes: upload text, upload files, list documents."""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import get_db
from app import models
from app.dependencies import get_current_user
from app.embedding import create_embedding
from app.chunking import chunk_text
from app.models import KnowledgeChunk
from app.file_parser import parse_uploaded_file, parse_raw_text

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.post("/upload")
def upload_knowledge(
    data: dict,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user)
):
    """Upload raw text as knowledge."""
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

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
            user_id=user_id,
            source_type=data.get("source_type", "document"),
            source_id=data.get("source_id", 0),
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
    user_id: int = Depends(get_current_user)
):
    """Upload a PDF, DOCX, TXT, CSV, or MD file as knowledge."""
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

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
            user_id=user_id,
            source_type=f"file:{parsed.file_type}",
            source_id=0,
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
    user_id: int = Depends(get_current_user)
):
    """List knowledge sources for this user."""
    sources = db.execute(
        text("""
            SELECT source_type, COUNT(*) as chunk_count,
                   MIN(created_at) as first_uploaded,
                   MAX(created_at) as last_uploaded
            FROM knowledge_chunks
            WHERE user_id = :user_id
            GROUP BY source_type
            ORDER BY MAX(created_at) DESC
        """),
        {"user_id": user_id}
    ).mappings().all()

    total = sum(s["chunk_count"] for s in sources)
    return {"total_chunks": total, "sources": [dict(s) for s in sources]}
