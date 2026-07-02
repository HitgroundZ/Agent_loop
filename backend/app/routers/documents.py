from hashlib import sha256
from pathlib import Path

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import Document, DocumentVersion, IdempotencyRecord
from app.services.parsers import SUPPORTED_EXTENSIONS, parse_file

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
        payload = _document_payload(existing, include_text=True)
        payload["duplicate"] = True
        return _store_and_return(db, idempotency_key, "documents.upload", payload, status.HTTP_200_OK)

    storage_path = settings.upload_path / f"{source_hash}{ext}"
    storage_path.write_bytes(content)

    document = Document(
        filename=file.filename or storage_path.name,
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

    try:
        parsed = parse_file(storage_path, ext)
        document.status = "completed"
        document.parser_name = parsed.parser_name
        document.extracted_text = parsed.text
        document.text_preview = parsed.text[:2000]
        document.metadata_json = parsed.metadata
        document.error_message = None
        db.add(
            DocumentVersion(
                document_id=document.id,
                version_no=1,
                source_hash=source_hash,
                storage_path=str(storage_path),
                extracted_chars=len(parsed.text),
                metadata_json=parsed.metadata,
            )
        )
    except Exception as exc:  # noqa: BLE001
        document.status = "failed"
        document.error_message = str(exc)
        document.text_preview = ""
        document.metadata_json = {"parser_error": str(exc)}

    db.commit()
    db.refresh(document)

    payload = _document_payload(document, include_text=True)
    payload["duplicate"] = False
    return _store_and_return(db, idempotency_key, "documents.upload", payload, status.HTTP_201_CREATED)


@router.get("")
def list_documents(db: Session = Depends(get_db)) -> dict:
    documents = db.scalars(select(Document).order_by(desc(Document.created_at))).all()
    return {"items": [_document_payload(document, include_text=False) for document in documents]}


@router.get("/{document_id}")
def get_document(document_id: str, db: Session = Depends(get_db)) -> dict:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return _document_payload(document, include_text=True)


@router.get("/{document_id}/chunks")
def list_document_chunks(document_id: str, db: Session = Depends(get_db)) -> dict:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return {
        "document_id": document.id,
        "items": [],
        "message": "Chunking starts on Day 2; Day 1 stores the extracted full text only.",
    }


@router.delete("/{document_id}")
def delete_document(
    document_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    cached = _get_idempotent_response(db, idempotency_key, "documents.delete")
    if cached:
        return cached

    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    storage_paths = [version.storage_path for version in document.versions]
    payload = {"deleted": True, "document_id": document.id}
    db.delete(document)
    db.commit()

    for storage_path in storage_paths:
        try:
            Path(storage_path).unlink(missing_ok=True)
        except OSError:
            pass

    return _store_and_return(db, idempotency_key, "documents.delete", payload, status.HTTP_200_OK)


def _document_payload(document: Document, include_text: bool) -> dict:
    payload = {
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
        "created_at": document.created_at.isoformat() if document.created_at else None,
        "updated_at": document.updated_at.isoformat() if document.updated_at else None,
    }
    if include_text:
        payload["extracted_text"] = document.extracted_text or ""
    return payload


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

