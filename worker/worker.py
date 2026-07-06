from datetime import datetime, timedelta, timezone
import logging
import os
import time

from openai import OpenAI
import psycopg
from psycopg.rows import dict_row
from redis import Redis
from redis.exceptions import RedisError


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

QUEUE_NAME = os.getenv("REDIS_EMBEDDING_QUEUE", "agent_loop:embedding_jobs")
BATCH_SIZE = min(int(os.getenv("EMBEDDING_BATCH_SIZE", "10")), 10)
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-v4")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))
DASHSCOPE_BASE_URL = os.getenv(
    "DASHSCOPE_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
)


def main() -> None:
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    redis = Redis.from_url(redis_url, decode_responses=True)
    logging.info("embedding worker started")

    while True:
        try:
            redis.set("agent_loop:worker:heartbeat", str(int(time.time())), ex=30)
            enqueue_due_jobs(redis)
            item = redis.blpop(QUEUE_NAME, timeout=5)
            if item:
                process_job(item[1])
        except RedisError as exc:
            logging.warning("redis unavailable: %s", exc)
            time.sleep(5)
        except Exception as exc:  # noqa: BLE001
            logging.exception("worker loop failed: %s", exc)
            time.sleep(5)


def enqueue_due_jobs(redis: Redis) -> None:
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id
                FROM embedding_jobs
                WHERE status IN ('pending', 'queued')
                  AND (next_run_at IS NULL OR next_run_at <= NOW())
                ORDER BY created_at
                LIMIT 20
                FOR UPDATE SKIP LOCKED
                """
            )
            rows = cur.fetchall()
            job_ids = [row["id"] for row in rows]
            if not job_ids:
                conn.commit()
                return
            cur.execute(
                "UPDATE embedding_jobs SET status = 'queued', updated_at = NOW() WHERE id = ANY(%s)",
                (job_ids,),
            )
            conn.commit()

    for job_id in job_ids:
        redis.rpush(QUEUE_NAME, job_id)


def process_job(job_id: str) -> None:
    logging.info("processing embedding job %s", job_id)
    try:
        job, chunks = claim_job(job_id)
        if not job:
            return
        if not chunks:
            complete_job(job_id, job["document_id"])
            return

        client = embedding_client()
        for batch in _batches(chunks, BATCH_SIZE):
            texts = [chunk["text"] for chunk in batch]
            response = client.embeddings.create(
                model=job["embedding_model"] or EMBEDDING_MODEL,
                input=texts,
                dimensions=job["embedding_dim"] or EMBEDDING_DIM,
                encoding_format="float",
            )
            embeddings = [item.embedding for item in response.data]
            if len(embeddings) != len(batch):
                raise RuntimeError("Embedding response size does not match request size")
            save_embeddings(batch, embeddings, job["embedding_model"], job["embedding_dim"])

        complete_job(job_id, job["document_id"])
    except Exception as exc:  # noqa: BLE001
        logging.exception("embedding job %s failed: %s", job_id, exc)
        fail_job(job_id, str(exc))


def claim_job(job_id: str) -> tuple[dict | None, list[dict]]:
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM embedding_jobs
                WHERE id = %s
                FOR UPDATE
                """,
                (job_id,),
            )
            job = cur.fetchone()
            if not job or job["status"] == "completed":
                conn.commit()
                return None, []
            if job["attempts"] >= job["max_attempts"]:
                cur.execute(
                    """
                    UPDATE embedding_jobs
                    SET status = 'failed', updated_at = NOW()
                    WHERE id = %s
                    """,
                    (job_id,),
                )
                conn.commit()
                return None, []

            cur.execute(
                """
                UPDATE embedding_jobs
                SET status = 'running',
                    attempts = attempts + 1,
                    last_error = NULL,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (job_id,),
            )
            cur.execute(
                """
                UPDATE documents
                SET status = 'embedding', updated_at = NOW()
                WHERE id = %s
                """,
                (job["document_id"],),
            )
            cur.execute(
                """
                UPDATE document_chunks
                SET embedding_status = 'embedding',
                    error_message = NULL,
                    updated_at = NOW()
                WHERE version_id = %s
                  AND embedding_status <> 'embedded'
                """,
                (job["version_id"],),
            )
            cur.execute(
                """
                SELECT id, text
                FROM document_chunks
                WHERE version_id = %s
                  AND embedding_status <> 'embedded'
                ORDER BY chunk_index
                """,
                (job["version_id"],),
            )
            chunks = cur.fetchall()
            conn.commit()
            job["attempts"] += 1
            return job, chunks


def save_embeddings(chunks: list[dict], embeddings: list[list[float]], model: str, dim: int) -> None:
    with connect_db() as conn:
        with conn.cursor() as cur:
            for chunk, embedding in zip(chunks, embeddings):
                if len(embedding) != dim:
                    raise RuntimeError(f"Embedding dimension mismatch: expected {dim}, got {len(embedding)}")
                cur.execute(
                    """
                    UPDATE document_chunks
                    SET embedding = %s::vector,
                        embedding_status = 'embedded',
                        embedding_model = %s,
                        embedding_dim = %s,
                        error_message = NULL,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (_vector_literal(embedding), model, dim, chunk["id"]),
                )
        conn.commit()


def complete_job(job_id: str, document_id: str) -> None:
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE embedding_jobs
                SET status = 'completed',
                    last_error = NULL,
                    next_run_at = NULL,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (job_id,),
            )
            cur.execute(
                """
                UPDATE documents
                SET status = 'indexed',
                    error_message = NULL,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (document_id,),
            )
        conn.commit()
    logging.info("embedding job %s completed", job_id)


def fail_job(job_id: str, message: str) -> None:
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM embedding_jobs WHERE id = %s FOR UPDATE",
                (job_id,),
            )
            job = cur.fetchone()
            if not job:
                conn.commit()
                return

            final_failure = job["attempts"] >= job["max_attempts"]
            next_status = "failed" if final_failure else "pending"
            next_run_at = None if final_failure else datetime.now(timezone.utc) + _retry_delay(job["attempts"])
            document_status = "embedding_failed" if final_failure else "embedding"

            cur.execute(
                """
                UPDATE embedding_jobs
                SET status = %s,
                    last_error = %s,
                    next_run_at = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (next_status, message[:4000], next_run_at, job_id),
            )
            cur.execute(
                """
                UPDATE document_chunks
                SET embedding_status = 'failed',
                    error_message = %s,
                    updated_at = NOW()
                WHERE version_id = %s
                  AND embedding_status <> 'embedded'
                """,
                (message[:4000], job["version_id"]),
            )
            cur.execute(
                """
                UPDATE documents
                SET status = %s,
                    error_message = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (document_status, message[:4000], job["document_id"]),
            )
        conn.commit()


def embedding_client() -> OpenAI:
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is not configured")
    return OpenAI(api_key=api_key, base_url=DASHSCOPE_BASE_URL)


def connect_db():
    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql://agent_loop:agent_loop@postgres:5432/agent_loop",
    ).replace("postgresql+psycopg://", "postgresql://", 1)
    return psycopg.connect(database_url, row_factory=dict_row)


def _batches(items: list[dict], size: int):
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(str(float(value)) for value in values) + "]"


def _retry_delay(attempts: int) -> timedelta:
    seconds = min(300, 30 * (2 ** max(attempts - 1, 0)))
    return timedelta(seconds=seconds)


if __name__ == "__main__":
    main()
