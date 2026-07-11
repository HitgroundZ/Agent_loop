from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.services.agent_loop import AgentLoopService, RetrievalStrategy
from app.services.retrieval import RetrievalFilters


router = APIRouter(prefix="/api/agent", tags=["agent"])


class AgentFiltersPayload(BaseModel):
    tenant_id: str | None = None
    workspace_id: str | None = None
    document_id: str | None = None
    document_ids: list[str] | None = None
    tags: list[str] | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None
    principal: str | None = None
    permission_subjects: list[str] | None = None


class AgentRunRequest(BaseModel):
    question: str | None = None
    message: str | None = None
    session_id: str | None = None
    strategy: RetrievalStrategy = "hybrid"
    top_k: int | None = Field(default=None, ge=1, le=50)
    filters: AgentFiltersPayload | None = None
    auto_approve: bool = True


@router.post("/runs")
def create_agent_run(
    request: AgentRunRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    question = (request.question or request.message or "").strip()
    if not question:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="question is required",
        )

    service = AgentLoopService(settings)
    return service.run(
        db=db,
        question=question,
        session_id=_blank_to_none(request.session_id),
        strategy=request.strategy,
        top_k=request.top_k,
        filters=_filters_from_payload(request.filters),
        auto_approve=request.auto_approve,
    )


@router.get("/runs/{run_id}")
def get_agent_run(
    run_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    service = AgentLoopService(settings)
    try:
        return service.get_run(db, run_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent run not found") from exc


@router.get("/sessions/{session_id}")
def get_agent_session(
    session_id: str,
    settings: Settings = Depends(get_settings),
) -> dict:
    service = AgentLoopService(settings)
    return service.get_session(session_id)


def _filters_from_payload(payload: AgentFiltersPayload | None) -> RetrievalFilters:
    if payload is None:
        return RetrievalFilters()
    return RetrievalFilters(
        tenant_id=_blank_to_none(payload.tenant_id),
        workspace_id=_blank_to_none(payload.workspace_id),
        document_id=_blank_to_none(payload.document_id),
        document_ids=[item.strip() for item in payload.document_ids or [] if item.strip()],
        tags=[tag.strip() for tag in payload.tags or [] if tag.strip()],
        created_from=payload.created_from,
        created_to=payload.created_to,
        principal=_blank_to_none(payload.principal),
        permission_subjects=[
            subject.strip() for subject in payload.permission_subjects or [] if subject.strip()
        ],
    )


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
