from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.services.retrieval import (
    RetrievalConfigurationError,
    RetrievalFilters,
    RetrievalService,
)


router = APIRouter(prefix="/api/retrieval", tags=["retrieval"])
Strategy = Literal["vector", "keyword", "hybrid"]


class RetrievalFiltersPayload(BaseModel):
    tenant_id: str | None = None
    workspace_id: str | None = None
    document_id: str | None = None
    document_ids: list[str] | None = None
    tags: list[str] | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None
    principal: str | None = None
    permission_subjects: list[str] | None = None


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    strategy: Strategy = "hybrid"
    top_k: int | None = None
    filters: RetrievalFiltersPayload | None = None
    rerank: bool = False


class CompareRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int | None = None
    filters: RetrievalFiltersPayload | None = None
    rerank: bool = False


@router.post("/search")
def search(
    request: SearchRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    service = RetrievalService(settings)
    try:
        return service.search(
            db=db,
            query=request.query,
            strategy=request.strategy,
            top_k=request.top_k,
            filters=_filters_from_payload(request.filters),
            rerank=request.rerank,
        )
    except RetrievalConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


@router.post("/compare")
def compare(
    request: CompareRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    service = RetrievalService(settings)
    filters = _filters_from_payload(request.filters)
    responses: dict[str, dict] = {}

    for strategy in ("vector", "keyword", "hybrid"):
        try:
            responses[strategy] = service.search(
                db=db,
                query=request.query,
                strategy=strategy,
                top_k=request.top_k,
                filters=filters,
                rerank=request.rerank,
            )
        except RetrievalConfigurationError as exc:
            rewritten_query = service.rewriter.rewrite(request.query)
            responses[strategy] = {
                "query": request.query,
                "rewritten_query": rewritten_query,
                "strategy": strategy,
                "top_k": service.top_k_policy.resolve(rewritten_query, request.top_k),
                "need_human_handoff": True,
                "results": [],
                "diagnostics": {
                    "error": str(exc),
                    "rerank": {
                        "rerank_requested": bool(request.rerank),
                        "rerank_applied": False,
                        "rerank_model": None,
                    },
                },
            }

    rewritten_query = service.rewriter.rewrite(request.query)
    return {
        "query": request.query,
        "rewritten_query": rewritten_query,
        "top_k": service.top_k_policy.resolve(rewritten_query, request.top_k),
        "need_human_handoff": all(response["need_human_handoff"] for response in responses.values()),
        "results": responses,
    }


def _filters_from_payload(payload: RetrievalFiltersPayload | None) -> RetrievalFilters:
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
