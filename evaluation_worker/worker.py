from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import time
from typing import Any, Awaitable, Callable
from uuid import uuid4

import httpx
from openai import AsyncOpenAI
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from redis import Redis
from redis.exceptions import RedisError


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

QUEUE_NAME = os.getenv("REDIS_EVALUATION_QUEUE", "agent_loop:evaluation_jobs")
DATASET_DIR = Path(os.getenv("EVAL_DATASET_DIR", "/evals/datasets"))
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000").rstrip("/")
MAX_CONCURRENCY = max(1, int(os.getenv("EVAL_MAX_CONCURRENCY", "2")))
CASE_TIMEOUT = float(os.getenv("EVAL_CASE_TIMEOUT_SECONDS", "120"))
MAX_RETRIES = max(0, int(os.getenv("EVAL_MAX_RETRIES", "2")))
STALE_SECONDS = max(300, int(os.getenv("EVAL_STALE_SECONDS", "900")))
METRIC_NAMES = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")


def main() -> None:
    redis = Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)
    logging.info("RAG evaluation worker 已启动，最大并发=%s", MAX_CONCURRENCY)
    while True:
        try:
            redis.set("agent_loop:evaluation_worker:heartbeat", str(int(time.time())), ex=30)
            enqueue_queued_runs(redis)
            item = redis.blpop(QUEUE_NAME, timeout=5)
            if item:
                process_run(item[1])
        except RedisError as exc:
            logging.warning("Redis 不可用：%s", exc)
            time.sleep(5)
        except Exception as exc:  # noqa: BLE001
            logging.exception("evaluation worker 循环失败：%s", exc)
            time.sleep(5)


def enqueue_queued_runs(redis: Redis) -> None:
    with connect_db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE evaluation_runs
            SET status = 'queued', updated_at = NOW(),
                error_message = COALESCE(error_message, 'worker 超时后重新排队')
            WHERE status = 'running'
              AND updated_at < NOW() - (%s * INTERVAL '1 second')
            """,
            (STALE_SECONDS,),
        )
        cur.execute(
            """
            SELECT id FROM evaluation_runs
            WHERE status = 'queued'
            ORDER BY created_at
            LIMIT 20
            """
        )
        run_ids = [row["id"] for row in cur.fetchall()]
        conn.commit()
    for run_id in run_ids:
        redis.rpush(QUEUE_NAME, run_id)


def process_run(run_id: str) -> None:
    run = claim_run(run_id)
    if not run:
        return
    logging.info("开始评测批次 %s，数据集=%s", run_id, run["dataset_id"])
    try:
        dataset = load_dataset(run["dataset_id"])
        if str(dataset["manifest"]["version"]) != str(run["dataset_version"]):
            raise RuntimeError("数据集版本已变化，请新建评测批次")
        asyncio.run(process_cases(run, dataset["cases"]))
        finish_run(run_id)
    except Exception as exc:  # noqa: BLE001
        logging.exception("评测批次 %s 失败：%s", run_id, exc)
        fail_run(run_id, str(exc))


def claim_run(run_id: str) -> dict | None:
    with connect_db() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM evaluation_runs WHERE id = %s FOR UPDATE", (run_id,))
        run = cur.fetchone()
        if not run or run["status"] != "queued":
            conn.commit()
            return None
        cur.execute(
            """
            UPDATE evaluation_runs
            SET status = 'running', started_at = COALESCE(started_at, NOW()),
                error_message = NULL, updated_at = NOW()
            WHERE id = %s
            """,
            (run_id,),
        )
        conn.commit()
        run["status"] = "running"
        return run


async def process_cases(run: dict, cases: list[dict]) -> None:
    existing = existing_case_ids(run["id"])
    pending = [case for case in cases if case["case_id"] not in existing]
    if not pending:
        return
    scorer = RagasScorer(
        model=run["judge_model"],
        embedding_model=run["embedding_model"],
    )
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    async with httpx.AsyncClient(timeout=CASE_TIMEOUT) as client:
        async def execute(index: int, case: dict) -> None:
            async with semaphore:
                result = await evaluate_case(run, case, index, client, scorer)
                await asyncio.to_thread(save_case_result, run["id"], result)

        await asyncio.gather(*(execute(index, case) for index, case in enumerate(pending, start=1)))


async def evaluate_case(
    run: dict,
    case: dict,
    index: int,
    client: httpx.AsyncClient,
    scorer: "RagasScorer",
) -> dict:
    started = time.perf_counter()
    payload = {
        "question": case["question"],
        "user_id": f"day10-eval-{run['id'][:8]}-{index}",
        "session_id": f"rag-eval-{run['id']}-{case['case_id']}",
        "strategy": run["strategy"],
        "retrieval_mode": "always",
        "top_k": run["top_k"],
        "filters": case.get("filters") or {},
        "auto_approve": False,
    }
    try:
        response = await with_retries(
            lambda: request_agent(client, payload),
            retries=MAX_RETRIES,
        )
    except Exception as exc:  # noqa: BLE001
        return pipeline_failure(case, started, f"Agent 请求失败：{exc}")

    answer = str(response.get("answer") or "")
    retrieval = response.get("retrieval_result") or {}
    candidates = list(retrieval.get("results") or [])[: int(run["top_k"])]
    retrieved_contexts = [
        {
            "context_id": item.get("context_id"),
            "document_name": item.get("document_name"),
            "chunk_id": item.get("chunk_id"),
            "chunk_index": item.get("chunk_index"),
            "rank": item.get("rank"),
            "score": item.get("score"),
            "text": item.get("snippet") or "",
        }
        for item in candidates
    ]
    reference_ids = [item["context_id"] for item in case["reference_contexts"]]
    retrieved_ids = [item.get("context_id") for item in retrieved_contexts if item.get("context_id")]
    hit = hit_at_k(retrieved_ids, reference_ids, int(run["top_k"]))
    pipeline_failed = response.get("current_state") == "failed" or not answer or not retrieved_contexts
    if pipeline_failed:
        scores = {name: 0.0 for name in METRIC_NAMES}
        reasons = {name: "端到端链路未产生可评测的回答和上下文。" for name in METRIC_NAMES}
        case_status = "pipeline_failed"
        error_message = response.get("error_message") or "未产生回答或检索上下文"
    else:
        try:
            scores, reasons, metric_errors = await asyncio.wait_for(
                scorer.score(
                    question=case["question"],
                    answer=answer,
                    reference=case["reference_answer"],
                    contexts=[item["text"] for item in retrieved_contexts],
                ),
                timeout=CASE_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001
            message = f"Ragas 评分失败：{exc}"
            scores = {name: None for name in METRIC_NAMES}
            reasons = {name: message for name in METRIC_NAMES}
            metric_errors = [message]
        case_status = "completed_with_errors" if metric_errors else "completed"
        error_message = "；".join(metric_errors) if metric_errors else None
    return {
        "case_id": case["case_id"],
        "agent_run_id": response.get("id"),
        "status": case_status,
        "question": case["question"],
        "reference_answer": case["reference_answer"],
        "answer": answer,
        "reference_contexts": case["reference_contexts"],
        "retrieved_contexts": retrieved_contexts,
        "scores": scores,
        "reasons": reasons,
        "hit_at_k": hit,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "error_message": error_message,
    }


async def request_agent(client: httpx.AsyncClient, payload: dict) -> dict:
    response = await client.post(f"{BACKEND_URL}/api/agent/runs", json=payload)
    response.raise_for_status()
    return response.json()


class RagasScorer:
    def __init__(self, *, model: str, embedding_model: str) -> None:
        self.model = model
        self.embedding_model = embedding_model
        self._metrics: dict[str, Any] | None = None

    def _initialize(self) -> dict[str, Any]:
        if self._metrics is not None:
            return self._metrics
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            raise RuntimeError("尚未配置 DASHSCOPE_API_KEY")
        from ragas.embeddings import OpenAIEmbeddings
        from ragas.llms import llm_factory
        from ragas.metrics.collections import AnswerRelevancy, ContextPrecision, ContextRecall, Faithfulness

        client = AsyncOpenAI(
            api_key=api_key,
            base_url=os.getenv(
                "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ),
            timeout=CASE_TIMEOUT,
            max_retries=0,
        )
        llm = llm_factory(self.model, client=client)
        embeddings = OpenAIEmbeddings(client=client, model=self.embedding_model)
        self._metrics = {
            "faithfulness": Faithfulness(llm=llm),
            "answer_relevancy": AnswerRelevancy(llm=llm, embeddings=embeddings),
            "context_precision": ContextPrecision(llm=llm),
            "context_recall": ContextRecall(llm=llm),
        }
        return self._metrics

    async def score(
        self,
        *,
        question: str,
        answer: str,
        reference: str,
        contexts: list[str],
    ) -> tuple[dict, dict, list[str]]:
        try:
            metrics = self._initialize()
        except Exception as exc:  # noqa: BLE001
            message = f"Ragas 初始化失败：{exc}"
            return ({name: None for name in METRIC_NAMES}, {name: message for name in METRIC_NAMES}, [message])

        factories: dict[str, Callable[[], Awaitable[Any]]] = {
            "faithfulness": lambda: metrics["faithfulness"].ascore(
                user_input=question, response=answer, retrieved_contexts=contexts
            ),
            "answer_relevancy": lambda: metrics["answer_relevancy"].ascore(
                user_input=question, response=answer
            ),
            "context_precision": lambda: metrics["context_precision"].ascore(
                user_input=question, reference=reference, retrieved_contexts=contexts
            ),
            "context_recall": lambda: metrics["context_recall"].ascore(
                user_input=question, reference=reference, retrieved_contexts=contexts
            ),
        }
        results = await asyncio.gather(
            *(with_retries(factory, retries=MAX_RETRIES) for factory in factories.values()),
            return_exceptions=True,
        )
        scores: dict[str, float | None] = {}
        reasons: dict[str, str] = {}
        errors: list[str] = []
        for name, result in zip(factories, results):
            if isinstance(result, BaseException):
                scores[name] = None
                reasons[name] = f"judge 调用失败：{result}"
                errors.append(f"{name}: {result}")
                continue
            value = float(result.value)
            scores[name] = max(0.0, min(1.0, value)) if math.isfinite(value) else None
            reasons[name] = str(result.reason or "")
            if scores[name] is None:
                errors.append(f"{name}: 返回了非有限分数")
        return scores, reasons, errors


async def with_retries(factory: Callable[[], Awaitable[Any]], *, retries: int) -> Any:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return await factory()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < retries:
                await asyncio.sleep(min(4.0, 0.5 * (2**attempt)))
    assert last_error is not None
    raise last_error


def hit_at_k(retrieved_ids: list[str], reference_ids: list[str], k: int) -> bool:
    references = set(reference_ids)
    return bool(references.intersection(retrieved_ids[: max(0, k)]))


def aggregate_case_results(rows: list[dict], total_cases: int) -> tuple[dict, dict]:
    metrics: dict[str, float | None] = {
        "hit_at_k": round(sum(bool(row["hit_at_k"]) for row in rows) / total_cases, 6)
        if total_cases else None
    }
    coverage: dict[str, dict] = {
        "hit_at_k": {"scored": len(rows), "total": total_cases}
    }
    for name in METRIC_NAMES:
        values = [float((row.get("scores") or {}).get(name)) for row in rows if (row.get("scores") or {}).get(name) is not None]
        metrics[name] = round(sum(values) / len(values), 6) if values else None
        coverage[name] = {"scored": len(values), "total": total_cases}
    return metrics, coverage


def save_case_result(run_id: str, result: dict) -> None:
    with connect_db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO evaluation_case_results (
                id, evaluation_run_id, case_id, agent_run_id, status, question,
                reference_answer, answer, reference_contexts, retrieved_contexts,
                scores, reasons, hit_at_k, latency_ms, error_message, created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW()
            ) ON CONFLICT (evaluation_run_id, case_id) DO NOTHING
            """,
            (
                str(uuid4()), run_id, result["case_id"], result.get("agent_run_id"), result["status"],
                result["question"], result["reference_answer"], result["answer"],
                Jsonb(result["reference_contexts"]), Jsonb(result["retrieved_contexts"]),
                Jsonb(result["scores"]), Jsonb(result["reasons"]), result["hit_at_k"],
                result["latency_ms"], result.get("error_message"),
            ),
        )
        cur.execute(
            """
            UPDATE evaluation_runs
            SET completed_cases = (
                    SELECT COUNT(*) FROM evaluation_case_results WHERE evaluation_run_id = %s
                ),
                failed_cases = (
                    SELECT COUNT(*) FROM evaluation_case_results
                    WHERE evaluation_run_id = %s AND status <> 'completed'
                ),
                updated_at = NOW()
            WHERE id = %s
            """,
            (run_id, run_id, run_id),
        )
        conn.commit()


def finish_run(run_id: str) -> None:
    with connect_db() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT hit_at_k, scores, status FROM evaluation_case_results WHERE evaluation_run_id = %s",
            (run_id,),
        )
        rows = cur.fetchall()
        cur.execute("SELECT total_cases FROM evaluation_runs WHERE id = %s", (run_id,))
        run = cur.fetchone()
        total = int(run["total_cases"] if run else len(rows))
        metrics, coverage = aggregate_case_results(rows, total)
        failed = sum(row["status"] != "completed" for row in rows)
        final_status = "completed_with_errors" if failed or len(rows) != total else "completed"
        cur.execute(
            """
            UPDATE evaluation_runs
            SET status = %s, metrics = %s, coverage = %s,
                completed_cases = %s, failed_cases = %s,
                completed_at = NOW(), updated_at = NOW()
            WHERE id = %s
            """,
            (final_status, Jsonb(metrics), Jsonb(coverage), len(rows), failed, run_id),
        )
        conn.commit()
    logging.info("评测批次 %s 完成，状态=%s", run_id, final_status)


def fail_run(run_id: str, message: str) -> None:
    with connect_db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE evaluation_runs
            SET status = 'failed', error_message = %s,
                completed_at = NOW(), updated_at = NOW()
            WHERE id = %s
            """,
            (message[:4000], run_id),
        )
        conn.commit()


def pipeline_failure(case: dict, started: float, message: str) -> dict:
    return {
        "case_id": case["case_id"], "agent_run_id": None, "status": "pipeline_failed",
        "question": case["question"], "reference_answer": case["reference_answer"],
        "answer": "", "reference_contexts": case["reference_contexts"],
        "retrieved_contexts": [], "scores": {name: 0.0 for name in METRIC_NAMES},
        "reasons": {name: message for name in METRIC_NAMES}, "hit_at_k": False,
        "latency_ms": int((time.perf_counter() - started) * 1000), "error_message": message,
    }


def existing_case_ids(run_id: str) -> set[str]:
    with connect_db() as conn, conn.cursor() as cur:
        cur.execute("SELECT case_id FROM evaluation_case_results WHERE evaluation_run_id = %s", (run_id,))
        return {row["case_id"] for row in cur.fetchall()}


def load_dataset(dataset_id: str) -> dict:
    root = DATASET_DIR.resolve()
    directory = (root / dataset_id).resolve()
    if root not in directory.parents:
        raise RuntimeError("非法数据集路径")
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    cases_path = directory / str(manifest.get("cases_file") or "cases.jsonl")
    cases = [json.loads(line) for line in cases_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {"manifest": manifest, "cases": cases}


def connect_db():
    database_url = os.getenv(
        "DATABASE_URL", "postgresql://agent_loop:agent_loop@postgres:5432/agent_loop"
    ).replace("postgresql+psycopg://", "postgresql://", 1)
    return psycopg.connect(database_url, row_factory=dict_row)


if __name__ == "__main__":
    main()
