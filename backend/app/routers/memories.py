from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.services.memory import MEMORY_CATEGORIES, MemoryService


router = APIRouter(prefix="/api/memories", tags=["memories"])
MemoryCategory = Literal[
    "event_summary",
    "scene",
    "user_profile",
    "human_correction",
]


class CreateMemoryRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=80)
    category: MemoryCategory
    content: str = Field(min_length=1, max_length=10000)
    source_message_id: str | None = None
    source_document_id: str | None = None
    metadata: dict = Field(default_factory=dict)


class UpdateMemoryRequest(BaseModel):
    enabled: bool


class CorrectMemoryRequest(BaseModel):
    corrected_content: str = Field(min_length=1, max_length=10000)
    reason: str | None = Field(default=None, max_length=1000)


@router.get("")
def list_memories(
    user_id: str = Query(min_length=1, max_length=80),
    category: str | None = Query(default=None),
    enabled: bool | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    if category and category not in MEMORY_CATEGORIES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="不支持的记忆类型")
    return MemoryService(settings).list_memories(
        db,
        user_id=user_id.strip(),
        category=category,
        enabled=enabled,
        limit=limit,
    )


@router.get("/messages")
def list_conversation_messages(
    user_id: str = Query(min_length=1, max_length=80),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    return MemoryService(settings).list_messages(db, user_id=user_id.strip(), limit=limit)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_memory(
    request: CreateMemoryRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        return MemoryService(settings).create_memory(
            db,
            user_id=request.user_id.strip(),
            category=request.category,
            content=request.content,
            source_message_id=_blank_to_none(request.source_message_id),
            source_document_id=_blank_to_none(request.source_document_id),
            metadata=request.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.patch("/{memory_id}")
def update_memory(
    memory_id: str,
    request: UpdateMemoryRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        return MemoryService(settings).set_enabled(db, memory_id, request.enabled)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="记忆不存在") from exc


@router.post("/{memory_id}/corrections", status_code=status.HTTP_201_CREATED)
def correct_memory(
    memory_id: str,
    request: CorrectMemoryRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        return MemoryService(settings).correct(
            db,
            memory_id,
            corrected_content=request.corrected_content,
            reason=request.reason,
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="记忆不存在") from exc


@router.delete("/{memory_id}")
def delete_memory(
    memory_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        return MemoryService(settings).delete(db, memory_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="记忆不存在") from exc


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
