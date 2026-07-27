from __future__ import annotations

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import EvaluationCaseResult, EvaluationRun


TERMINAL_EVALUATION_STATES = {"completed", "completed_with_errors", "failed"}


def enqueue_evaluation_run(settings: Settings, run_id: str) -> bool:
    try:
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        redis.rpush(settings.redis_evaluation_queue, run_id)
        return True
    except RedisError:
        # Worker 会定期扫描 queued 记录，因此 Redis 短暂不可用不会丢任务。
        return False


def evaluation_run_payload(run: EvaluationRun, *, include_cases: bool = False) -> dict:
    payload = {
        "id": run.id,
        "dataset_id": run.dataset_id,
        "dataset_version": run.dataset_version,
        "status": run.status,
        "strategy": run.strategy,
        "top_k": run.top_k,
        "judge_model": run.judge_model,
        "embedding_model": run.embedding_model,
        "config_snapshot": run.config_snapshot or {},
        "metrics": run.metrics or {},
        "coverage": run.coverage or {},
        "total_cases": run.total_cases,
        "completed_cases": run.completed_cases,
        "failed_cases": run.failed_cases,
        "progress": round(run.completed_cases / run.total_cases, 4) if run.total_cases else 0.0,
        "error_message": run.error_message,
        "created_at": _iso(run.created_at),
        "started_at": _iso(run.started_at),
        "completed_at": _iso(run.completed_at),
        "updated_at": _iso(run.updated_at),
    }
    if include_cases:
        payload["cases"] = [evaluation_case_payload(item) for item in run.case_results]
    return payload


def evaluation_case_payload(item: EvaluationCaseResult) -> dict:
    return {
        "id": item.id,
        "case_id": item.case_id,
        "agent_run_id": item.agent_run_id,
        "status": item.status,
        "question": item.question,
        "reference_answer": item.reference_answer,
        "answer": item.answer,
        "reference_contexts": item.reference_contexts or [],
        "retrieved_contexts": item.retrieved_contexts or [],
        "scores": item.scores or {},
        "reasons": item.reasons or {},
        "hit_at_k": item.hit_at_k,
        "latency_ms": item.latency_ms,
        "error_message": item.error_message,
        "created_at": _iso(item.created_at),
        "updated_at": _iso(item.updated_at),
    }


def get_evaluation_run(db: Session, run_id: str) -> EvaluationRun:
    run = db.scalar(select(EvaluationRun).where(EvaluationRun.id == run_id))
    if run is None:
        raise KeyError(run_id)
    return run


def _iso(value) -> str | None:
    return value.isoformat() if value else None
