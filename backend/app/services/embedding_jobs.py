from datetime import datetime, timezone
import logging

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Document, DocumentChunk, DocumentVersion, EmbeddingJob


logger = logging.getLogger(__name__)


def ensure_embedding_job(
    db: Session,
    document: Document,
    version: DocumentVersion,
    settings: Settings,
    retry_failed: bool = False,
) -> tuple[EmbeddingJob, bool]:
    key = embedding_idempotency_key(document, version, settings)
    job = db.scalar(select(EmbeddingJob).where(EmbeddingJob.idempotency_key == key))
    if job:
        if retry_failed and job.status == "failed":
            job.status = "pending"
            job.attempts = 0
            job.last_error = None
            job.next_run_at = _utcnow()
            document.status = "embedding"
            db.execute(
                update(DocumentChunk)
                .where(DocumentChunk.version_id == version.id)
                .where(DocumentChunk.embedding_status == "failed")
                .values(embedding_status="pending", error_message=None)
            )
            db.flush()
            return job, True
        return job, False

    job = EmbeddingJob(
        document_id=document.id,
        version_id=version.id,
        idempotency_key=key,
        status="pending",
        attempts=0,
        max_attempts=3,
        embedding_model=settings.embedding_model,
        embedding_dim=settings.embedding_dim,
        next_run_at=_utcnow(),
    )
    db.add(job)
    db.flush()
    return job, True


def enqueue_embedding_job(settings: Settings, job_id: str) -> bool:
    try:
        client = Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=2)
        client.rpush(settings.redis_embedding_queue, job_id)
        return True
    except RedisError as exc:
        logger.warning("向量化任务 %s 已保存，但未能入队：%s", job_id, exc)
        return False


def embedding_idempotency_key(document: Document, version: DocumentVersion, settings: Settings) -> str:
    return (
        f"embedding:{document.id}:{version.id}:"
        f"{settings.embedding_model}:{settings.embedding_dim}:{document.source_hash}"
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
