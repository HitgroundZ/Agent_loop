from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import EvaluationRun, IdempotencyRecord, new_id
from app.services.evaluation_datasets import (
    EvaluationDatasetError,
    dataset_summary,
    list_evaluation_datasets,
    load_evaluation_dataset,
    validate_dataset_against_knowledge_base,
)
from app.services.evaluations import (
    enqueue_evaluation_run,
    evaluation_run_payload,
    get_evaluation_run,
)


router = APIRouter(prefix="/api/evaluations", tags=["evaluations"])
Strategy = Literal["vector", "keyword", "hybrid"]


class EvaluationRunRequest(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=120)
    strategy: Strategy = "hybrid"
    top_k: int | None = Field(default=None, ge=1, le=50)


@router.get("/datasets")
def list_datasets(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    items = list_evaluation_datasets(settings.eval_dataset_dir, db)
    return {"items": items, "total": len(items)}


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED)
def create_evaluation_run(
    request: EvaluationRunRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    key = (idempotency_key or "").strip()
    if not key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="必须提供 Idempotency-Key")
    if len(key) > 160:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Idempotency-Key 不能超过 160 个字符")

    cached = db.get(IdempotencyRecord, key)
    if cached:
        if cached.scope != "evaluations.create_run":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Idempotency-Key 已用于其他操作")
        return cached.response_json

    try:
        dataset = load_evaluation_dataset(settings.eval_dataset_dir, request.dataset_id)
    except EvaluationDatasetError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    validation_errors = validate_dataset_against_knowledge_base(db, dataset)
    if validation_errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "评测数据集与当前知识库不匹配", "errors": validation_errors},
        )

    top_k = request.top_k or int(dataset.manifest.get("default_top_k") or 5)
    run = EvaluationRun(
        id=new_id(),
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.version,
        status="queued",
        strategy=request.strategy,
        top_k=top_k,
        judge_model=settings.eval_llm_model,
        embedding_model=settings.eval_embedding_model,
        total_cases=len(dataset.cases),
        config_snapshot={
            "dataset": dataset_summary(dataset),
            "strategy": request.strategy,
            "top_k": top_k,
            "rerank": True,
            "metrics": [
                "hit_at_k",
                "faithfulness",
                "answer_relevancy",
                "context_precision",
                "context_recall",
            ],
        },
    )
    db.add(run)
    db.flush()
    payload = evaluation_run_payload(run)
    db.add(IdempotencyRecord(
        key=key,
        scope="evaluations.create_run",
        status_code=status.HTTP_202_ACCEPTED,
        response_json=payload,
    ))
    db.commit()
    enqueue_evaluation_run(settings, run.id)
    return payload


@router.get("/runs")
def list_evaluation_runs(
    limit: int = Query(default=30, ge=1, le=100),
    dataset_id: str | None = Query(default=None),
    strategy: Strategy | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(EvaluationRun)
    if dataset_id:
        stmt = stmt.where(EvaluationRun.dataset_id == dataset_id)
    if strategy:
        stmt = stmt.where(EvaluationRun.strategy == strategy)
    items = db.scalars(stmt.order_by(EvaluationRun.created_at.desc()).limit(limit)).all()
    return {"items": [evaluation_run_payload(item) for item in items], "total": len(items)}


@router.get("/runs/{run_id}")
def get_evaluation_run_detail(
    run_id: str,
    db: Session = Depends(get_db),
) -> dict:
    try:
        run = get_evaluation_run(db, run_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="评测批次不存在") from exc
    return evaluation_run_payload(run, include_cases=True)
