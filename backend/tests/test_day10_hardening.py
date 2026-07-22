from __future__ import annotations

from hashlib import sha256
import json
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from redis.exceptions import RedisError
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.main import app
from app.models import (
    AgentRun,
    Document,
    DocumentChunk,
    DocumentVersion,
    IdempotencyRecord,
    new_id,
)
from app.services.agent_loop import AgentLoopService
from app.services.agent_model import ModelTurn
from app.services.embedding_jobs import ensure_embedding_job
from app.services.retrieval import RetrievalFilters, RetrievalService
from app.services.tooling import (
    RolePolicy,
    ToolExecutor,
    ToolRuntime,
    derive_intent_authorization,
)


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://agent_loop:agent_loop@127.0.0.1:5432/agent_loop",
)


class InMemoryObjectStorage:
    """上传幂等测试只验证业务边界，不依赖真实 MinIO。"""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_bytes(self, key: str, content: bytes, content_type: str) -> None:
        self.objects[key] = content

    def put_text(self, key: str, content: str) -> None:
        self.objects[key] = content.encode("utf-8")

    def delete_many(self, keys: list[str]) -> None:
        for key in keys:
            self.objects.pop(key, None)


class HallucinatedCitationModel:
    """工具返回真实 C1 后，故意输出不存在的 C999。"""

    def complete(self, messages: list[dict], *, tools=None, tool_choice=None) -> ModelTurn:
        answer = "部署已经完成。[C999]"
        return ModelTurn(
            content=answer,
            tool_calls=[],
            token_usage={
                "input_tokens": 4,
                "output_tokens": 4,
                "total_tokens": 8,
                "estimated": False,
            },
            raw_assistant_message={"role": "assistant", "content": answer},
        )


class Day10HardeningIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = create_engine(DATABASE_URL, pool_pre_ping=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()

    def setUp(self) -> None:
        suffix = uuid4().hex[:10]
        self.user_id = f"day10-user-{suffix}"
        self.session_ids: list[str] = []
        self.run_ids: list[str] = []
        self.document_ids: list[str] = []
        self.idempotency_keys: list[str] = []
        self.settings = Settings(
            database_url=DATABASE_URL,
            tool_default_roles="user",
            tool_role_assignments=json.dumps({self.user_id: ["operator", "approver"]}),
        )

    def tearDown(self) -> None:
        with Session(self.engine) as db:
            if self.run_ids:
                db.execute(delete(AgentRun).where(AgentRun.id.in_(self.run_ids)))
            if self.document_ids:
                db.execute(delete(Document).where(Document.id.in_(self.document_ids)))
            if self.idempotency_keys:
                db.execute(
                    delete(IdempotencyRecord).where(
                        IdempotencyRecord.key.in_(self.idempotency_keys)
                    )
                )
            db.commit()
        self._clear_test_redis_keys()

    def test_document_upload_replays_the_original_response_once(self) -> None:
        key = f"day10-upload-{uuid4()}"
        self.idempotency_keys.append(key)
        content = f"# Day 10\n\n幂等验收标识：{uuid4()}".encode("utf-8")
        storage = InMemoryObjectStorage()
        app.dependency_overrides[get_settings] = lambda: self.settings
        try:
            with (
                patch(
                    "app.routers.documents.get_object_storage",
                    return_value=storage,
                ),
                patch(
                    "app.routers.documents.enqueue_embedding_job",
                    return_value=True,
                ) as enqueue,
                TestClient(app) as client,
            ):
                request = {
                    "files": {"file": ("day10.md", content, "text/markdown")},
                    "data": {
                        "tenant_id": "day10",
                        "workspace_id": "hardening",
                        "tags": '["day10", "idempotency"]',
                        "permissions": json.dumps({"subjects": [self.user_id]}),
                    },
                    "headers": {"Idempotency-Key": key},
                }
                first = client.post("/api/documents/upload", **request)
                second = client.post("/api/documents/upload", **request)
        finally:
            app.dependency_overrides.pop(get_settings, None)

        self.assertEqual(201, first.status_code, first.text)
        self.assertEqual(201, second.status_code, second.text)
        self.assertEqual(first.json(), second.json())
        self.assertFalse(first.json()["duplicate"])
        self.document_ids.append(first.json()["id"])
        enqueue.assert_called_once()

        with Session(self.engine) as db:
            self.assertEqual(
                1,
                db.scalar(
                    select(func.count())
                    .select_from(Document)
                    .where(Document.source_hash == sha256(content).hexdigest())
                ),
            )

    def test_role_assignment_supports_scoped_eval_subject_pattern(self) -> None:
        policy = RolePolicy(
            Settings(
                tool_default_roles="user",
                tool_role_assignments='{"day10-eval-*":["operator","approver"]}',
            )
        )
        self.assertIn("approval.decide", policy.permissions_for("day10-eval-run-1"))
        self.assertNotIn("approval.decide", policy.permissions_for("untrusted-eval-run-1"))

    def test_failed_embedding_job_can_be_reset_once_for_retry(self) -> None:
        with Session(self.engine, expire_on_commit=False) as db:
            document, version, chunk = self._add_document_chunk(
                db,
                text="embedding retry contract",
                permissions={},
            )
            job, created = ensure_embedding_job(
                db, document, version, self.settings
            )
            self.assertTrue(created)
            job.status = "failed"
            job.attempts = job.max_attempts
            job.last_error = "temporary provider failure"
            chunk.embedding_status = "failed"
            chunk.error_message = job.last_error
            document.status = "embedding_failed"
            db.commit()

            reset_job, should_enqueue = ensure_embedding_job(
                db,
                document,
                version,
                self.settings,
                retry_failed=True,
            )
            db.commit()
            db.refresh(chunk)

            self.assertEqual(job.id, reset_job.id)
            self.assertTrue(should_enqueue)
            self.assertEqual("pending", reset_job.status)
            self.assertEqual(0, reset_job.attempts)
            self.assertIsNone(reset_job.last_error)
            self.assertIsNotNone(reset_job.next_run_at)
            self.assertEqual("pending", chunk.embedding_status)
            self.assertIsNone(chunk.error_message)

            same_job, duplicate_enqueue = ensure_embedding_job(
                db,
                document,
                version,
                self.settings,
                retry_failed=True,
            )
            self.assertEqual(job.id, same_job.id)
            self.assertFalse(duplicate_enqueue)

    def test_retrieval_filters_chunks_by_permission_subject(self) -> None:
        unique_term = f"权限过滤验收{uuid4().hex[:8]}"
        with Session(self.engine, expire_on_commit=False) as db:
            allowed, _, _ = self._add_document_chunk(
                db,
                text=f"{unique_term} 允许当前主体读取",
                permissions={"subjects": [self.user_id]},
            )
            denied, _, _ = self._add_document_chunk(
                db,
                text=f"{unique_term} 不允许当前主体读取",
                permissions={"subjects": ["another-principal"]},
            )
            db.commit()

            result = RetrievalService(self.settings).search(
                db,
                query=unique_term,
                strategy="keyword",
                filters=RetrievalFilters(
                    tenant_id="day10",
                    workspace_id="hardening",
                    principal=self.user_id,
                ),
                top_k=10,
            )

        result_ids = {item["document_id"] for item in result["results"]}
        self.assertIn(allowed.id, result_ids)
        self.assertNotIn(denied.id, result_ids)

    def test_unknown_citation_forces_human_handoff(self) -> None:
        session_id = f"day10-hallucination-{uuid4().hex[:8]}"
        self.session_ids.append(session_id)
        retrieval_result = {
            "query": "根据文档说明部署状态",
            "rewritten_query": "根据文档说明部署状态",
            "strategy": "keyword",
            "top_k": 5,
            "need_human_handoff": False,
            "results": [
                {
                    "document_id": "day10-document",
                    "document_name": "Day10 验收文档",
                    "chunk_id": "day10-chunk",
                    "chunk_index": 0,
                    "snippet": "部署前必须完成数据库迁移。",
                    "score": 0.96,
                    "retrieval_source": "keyword",
                }
            ],
            "diagnostics": {"rerank": {"rerank_applied": True}},
        }
        with (
            patch(
                "app.services.tooling.RetrievalService.search",
                return_value=retrieval_result,
            ),
            Session(self.engine, expire_on_commit=False) as db,
        ):
            result = AgentLoopService(
                self.settings,
                model_client=HallucinatedCitationModel(),
            ).run(
                db,
                question="根据文档说明部署状态",
                user_id=self.user_id,
                session_id=session_id,
                strategy="keyword",
                retrieval_mode="always",
            )
            self.run_ids.append(result["id"])

        self.assertEqual("escalated_to_human", result["current_state"])
        self.assertEqual([], result["citations"])
        self.assertTrue(result["evaluation"]["need_human_handoff"])
        self.assertTrue(result["evaluation"]["route_requires_document_citation"])
        self.assertIn("[C999]", result["answer"])

    def test_untrusted_document_instruction_cannot_authorize_side_effect(self) -> None:
        session_id = f"day10-injection-{uuid4().hex[:8]}"
        self.session_ids.append(session_id)
        with Session(self.engine, expire_on_commit=False) as db:
            run = AgentRun(
                id=new_id(),
                user_id=self.user_id,
                session_id=session_id,
                question="根据文档概括内容",
                status="acting",
                retrieval_strategy="keyword",
            )
            db.add(run)
            db.commit()
            self.run_ids.append(run.id)

            runtime = ToolRuntime(
                db=db,
                settings=self.settings,
                run=run,
                source_message_id="untrusted-document",
                question=run.question,
                strategy="keyword",
                top_k=5,
                filters=RetrievalFilters(),
                citation_catalog={},
            )
            action = ToolExecutor(self.settings).prepare(
                runtime,
                tool_call_id="day10-injected-delete",
                tool_name="delete_document",
                arguments={
                    "document_id": str(uuid4()),
                    "reason": "知识库片段要求删除",
                },
                intent=derive_intent_authorization(run.question),
                auto_approve=True,
            )

        self.assertEqual("blocked", action.status)
        self.assertIn("没有授权", action.error)

    def _add_document_chunk(
        self,
        db: Session,
        *,
        text: str,
        permissions: dict,
    ) -> tuple[Document, DocumentVersion, DocumentChunk]:
        source_hash = sha256(f"{uuid4()}:{text}".encode("utf-8")).hexdigest()
        document = Document(
            id=new_id(),
            filename=f"day10-{uuid4().hex[:8]}.md",
            content_type="text/markdown",
            file_ext=".md",
            source_hash=source_hash,
            size_bytes=len(text.encode("utf-8")),
            status="chunked",
            tenant_id="day10",
            workspace_id="hardening",
            tags=["day10"],
            permissions=permissions,
            metadata_json={},
        )
        version = DocumentVersion(
            id=new_id(),
            document=document,
            version_no=1,
            source_hash=source_hash,
            extracted_chars=len(text),
            metadata_json={},
        )
        chunk = DocumentChunk(
            id=new_id(),
            document=document,
            version=version,
            chunk_index=0,
            source_hash=source_hash,
            tenant_id=document.tenant_id,
            workspace_id=document.workspace_id,
            tags=document.tags,
            permissions=permissions,
            text=text,
            metadata_json={},
            embedding_status="pending",
        )
        db.add_all([document, version, chunk])
        db.flush()
        self.document_ids.append(document.id)
        return document, version, chunk

    def _clear_test_redis_keys(self) -> None:
        try:
            client = AgentLoopService(
                self.settings,
                model_client=HallucinatedCitationModel(),
            ).session_store.client
            keys = [
                *(f"agent_loop:sessions:{session_id}" for session_id in self.session_ids),
                f"agent_loop:memory_version:{self.user_id}",
            ]
            keys.extend(
                client.scan_iter(match=f"agent_loop:memory_cache:{self.user_id}:*")
            )
            if keys:
                client.delete(*keys)
        except RedisError:
            pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
