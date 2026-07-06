"""
文件处理相关路由：
文件上传、获取文件列表、获取某个文件、删除某个文件、获取切片片段。
"""
from collections import Counter
from hashlib import sha256
from pathlib import Path
import tempfile

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import (
    Document,
    DocumentChunk,
    DocumentVersion,
    IdempotencyRecord,
    new_id,
)
from app.services.chunking import build_chunks
from app.services.embedding_jobs import ensure_embedding_job, enqueue_embedding_job
from app.services.parsers import SUPPORTED_EXTENSIONS, parse_file
from app.services.storage import get_object_storage


router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    cached = _get_idempotent_response(db, idempotency_key, "documents.upload")
    if cached:
        return cached

    ext = Path(file.filename or "").suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={
                "message": "Unsupported document type",
                "supported_extensions": sorted(SUPPORTED_EXTENSIONS),
            },
        )

    content = await file.read()
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File is larger than {settings.max_upload_bytes} bytes",
        )

    source_hash = sha256(content).hexdigest()
    existing = db.scalar(select(Document).where(Document.source_hash == source_hash))
    if existing:
        if _is_legacy_unindexed_document(existing):
            db.delete(existing)
            db.commit()
        else:
            payload = _document_payload(existing)
            payload["duplicate"] = True
            return _store_and_return(db, idempotency_key, "documents.upload", payload, status.HTTP_200_OK)

    source_object_key = f"documents/{source_hash}/source{ext}"
    document = Document(
        filename=file.filename or f"{source_hash}{ext}",
        content_type=file.content_type or "application/octet-stream",
        file_ext=ext,
        source_hash=source_hash,
        size_bytes=len(content),
        status="parsing",
        metadata_json={},
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    storage = get_object_storage(settings)
    should_enqueue = False
    job_id: str | None = None
    temp_path: Path | None = None
    uploaded_object_keys: list[str] = []

    try:
        storage.put_bytes(source_object_key, content, document.content_type)
        uploaded_object_keys.append(source_object_key)
        temp_path = _write_temp_file(content, ext)
        parsed = parse_file(temp_path, ext)

        version = DocumentVersion(
            id=new_id(),
            document_id=document.id,
            version_no=1,
            source_hash=source_hash,
            source_object_key=source_object_key,
            extracted_text_object_key=None,
            extracted_chars=len(parsed.text),
            metadata_json={},
        )
        version.extracted_text_object_key = (
            f"documents/{document.id}/versions/{version.id}/extracted.txt"
        )
        storage.put_text(version.extracted_text_object_key, parsed.text)
        uploaded_object_keys.append(version.extracted_text_object_key)

        document.parser_name = parsed.parser_name
        document.text_preview = parsed.text[:2000]
        document.metadata_json = parsed.metadata
        document.error_message = None
        document.status = "chunked"
        version.metadata_json = {
            **parsed.metadata,
            "object_keys": {
                "source": source_object_key,
                "extracted_text": version.extracted_text_object_key,
            },
        }
        db.add(version)
        db.flush()

        chunks = build_chunks(
            parsed.text,
            parsed.metadata,
            max_chars=settings.chunk_max_chars,
            overlap_chars=settings.chunk_overlap_chars,
        )
        for chunk_index, chunk in enumerate(chunks):
            db.add(
                DocumentChunk(
                    document_id=document.id,
                    version_id=version.id,
                    chunk_index=chunk_index,
                    page=chunk.page,
                    heading=chunk.heading,
                    source_hash=source_hash,
                    text=chunk.text,
                    metadata_json=chunk.metadata,
                    embedding_status="pending",
                )
            )

        if chunks:
            job, should_enqueue = ensure_embedding_job(db, document, version, settings)
            job_id = job.id
            document.status = "embedding"
        else:
            document.status = "completed"

        db.commit()
    except Exception as exc:
        db.rollback()
        document = db.get(Document, document.id)
        document.status = "failed"
        document.error_message = str(exc)
        document.text_preview = ""
        document.metadata_json = {"parser_error": str(exc)}
        db.commit()
        storage.delete_many(uploaded_object_keys)
    finally:
        if temp_path:
            temp_path.unlink(missing_ok=True)

    if should_enqueue and job_id:
        enqueue_embedding_job(settings, job_id)

    db.refresh(document)
    payload = _document_payload(document)
    payload["duplicate"] = False
    return _store_and_return(db, idempotency_key, "documents.upload", payload, status.HTTP_201_CREATED)


@router.get("")
def list_documents(db: Session = Depends(get_db)) -> dict:
    documents = db.scalars(select(Document).order_by(desc(Document.created_at))).all()
    return {"items": [_document_payload(document) for document in documents]}


@router.get("/{document_id}")
def get_document(document_id: str, db: Session = Depends(get_db)) -> dict:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return _document_payload(document)


@router.get("/{document_id}/chunks")
def list_document_chunks(document_id: str, db: Session = Depends(get_db)) -> dict:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    chunks = db.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.document_id == document.id)
        .order_by(DocumentChunk.chunk_index)
    ).all()
    return {
        "document_id": document.id,
        "version_id": _latest_version(document).id if _latest_version(document) else None,
        "items": [_chunk_payload(chunk) for chunk in chunks],
    }


@router.post("/{document_id}/embedding-jobs")
def retry_embedding_job(
    document_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    version = _latest_version(document)
    if not version:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Document has no parsed version")
    if not document.chunks:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Document has no chunks")

    job, should_enqueue = ensure_embedding_job(db, document, version, settings, retry_failed=True)
    if should_enqueue:
        document.status = "embedding"
    db.commit()

    enqueued = enqueue_embedding_job(settings, job.id) if should_enqueue else False
    return {
        "job": _embedding_job_payload(job),
        "enqueued": enqueued,
        "created_or_reset": should_enqueue,
    }


@router.delete("/{document_id}")
def delete_document(
    document_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    cached = _get_idempotent_response(db, idempotency_key, "documents.delete")
    if cached:
        return cached

    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    object_keys = []
    for version in document.versions:
        if version.source_object_key:
            object_keys.append(version.source_object_key)
        if version.extracted_text_object_key:
            object_keys.append(version.extracted_text_object_key)

    payload = {"deleted": True, "document_id": document.id}
    db.delete(document)
    db.commit()
    get_object_storage(settings).delete_many(object_keys)

    return _store_and_return(db, idempotency_key, "documents.delete", payload, status.HTTP_200_OK)


def _document_payload(document: Document) -> dict:
    chunks = list(document.chunks)
    summary = Counter(chunk.embedding_status for chunk in chunks)
    latest_version = _latest_version(document)
    latest_job = _latest_embedding_job(document)
    return {
        "id": document.id,
        "filename": document.filename,
        "content_type": document.content_type,
        "file_ext": document.file_ext,
        "source_hash": document.source_hash,
        "size_bytes": document.size_bytes,
        "status": document.status,
        "parser_name": document.parser_name,
        "error_message": document.error_message,
        "text_preview": document.text_preview or "",
        "metadata": document.metadata_json or {},
        "chunk_count": len(chunks),
        "embedding_summary": dict(summary),
        "current_version_id": latest_version.id if latest_version else None,
        "embedding_job": _embedding_job_payload(latest_job) if latest_job else None,
        "created_at": document.created_at.isoformat() if document.created_at else None,
        "updated_at": document.updated_at.isoformat() if document.updated_at else None,
    }


def _chunk_payload(chunk: DocumentChunk) -> dict:
    return {
        "id": chunk.id,
        "document_id": chunk.document_id,
        "version_id": chunk.version_id,
        "chunk_index": chunk.chunk_index,
        "page": chunk.page,
        "heading": chunk.heading,
        "source_hash": chunk.source_hash,
        "text": chunk.text,
        "metadata": chunk.metadata_json or {},
        "embedding": {
            "status": chunk.embedding_status,
            "model": chunk.embedding_model,
            "dim": chunk.embedding_dim,
            "has_vector": chunk.embedding is not None,
            "error_message": chunk.error_message,
        },
        "created_at": chunk.created_at.isoformat() if chunk.created_at else None,
        "updated_at": chunk.updated_at.isoformat() if chunk.updated_at else None,
    }


def _embedding_job_payload(job) -> dict:
    return {
        "id": job.id,
        "status": job.status,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "embedding_model": job.embedding_model,
        "embedding_dim": job.embedding_dim,
        "last_error": job.last_error,
        "next_run_at": job.next_run_at.isoformat() if job.next_run_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


def _latest_version(document: Document) -> DocumentVersion | None:
    if not document.versions:
        return None
    return max(document.versions, key=lambda version: version.version_no)


def _latest_embedding_job(document: Document):
    if not document.embedding_jobs:
        return None
    return max(document.embedding_jobs, key=lambda job: job.created_at)


def _is_legacy_unindexed_document(document: Document) -> bool:
    latest_version = _latest_version(document)
    return (
        not document.chunks
        and latest_version is not None
        and latest_version.source_object_key is None
        and latest_version.extracted_text_object_key is None
    )


def _write_temp_file(content: bytes, ext: str) -> Path:
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as temp_file:
        temp_file.write(content)
        return Path(temp_file.name)


def _get_idempotent_response(db: Session, key: str | None, scope: str) -> JSONResponse | None:
    if not key:
        return None
    record = db.get(IdempotencyRecord, key)
    if not record:
        return None
    if record.scope != scope:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency-Key was already used for a different operation",
        )
    return JSONResponse(status_code=record.status_code, content=record.response_json)


def _store_and_return(
    db: Session,
    key: str | None,
    scope: str,
    payload: dict,
    status_code: int,
) -> JSONResponse:
    if key:
        db.merge(
            IdempotencyRecord(
                key=key,
                scope=scope,
                status_code=status_code,
                response_json=payload,
            )
        )
        db.commit()
    return JSONResponse(status_code=status_code, content=payload)
