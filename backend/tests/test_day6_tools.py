from __future__ import annotations

import json
import os
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from redis.exceptions import RedisError
from sqlalchemy import create_engine, delete, func, inspect, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.main import app
from app.models import AgentRun, IdempotencyRecord, LongTermMemory, ToolAction, ToolOutbox, new_id
from app.services.agent_loop import AgentLoopService
from app.services.agent_model import ModelTurn
from app.services.memory import MemoryService
from app.services.retrieval import DashScopeReranker, RetrievalConfigurationError, RetrievalFilters
from app.services.tooling import (
    RolePolicy,
    ToolExecutor,
    ToolRegistry,
    ToolRuntime,
    _call_webhook,
    action_payload,
    derive_intent_authorization,
)


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://agent_loop:agent_loop@127.0.0.1:5432/agent_loop",
)


class ContextAwareFakeModel:
    """不访问网络；工具结果出现后生成带真实引用的最终答案。"""

    def __init__(self) -> None:
        self.exposed_tools: list[list[str]] = []

    def complete(self, messages: list[dict], *, tools=None, tool_choice=None) -> ModelTurn:
        self.exposed_tools.append([
            item["function"]["name"] for item in (tools or [])
        ])
        answer = "这是无需知识库的直接回答。"
        if messages and messages[-1].get("role") == "tool":
            payload = json.loads(messages[-1].get("content") or "{}")
            items = payload.get("items") or []
            if items and str(items[0].get("citation_id", "")).startswith("M"):
                answer = f"根据记忆，你喜欢手冲咖啡。[{items[0]['citation_id']}]"
            elif items and str(items[0].get("citation_id", "")).startswith("C"):
                answer = f"文档给出的结论可核验。[{items[0]['citation_id']}]"
            elif payload.get("status") == "rejected":
                answer = "工具操作已被人工拒绝，没有执行。"
            else:
                answer = "工具操作已处理。"
        return ModelTurn(
            content=answer,
            tool_calls=[],
            token_usage={
                "input_tokens": 5, "output_tokens": 5, "total_tokens": 10,
                "estimated": False,
            },
            raw_assistant_message={"role": "assistant", "content": answer},
        )


class Day56AgentToolsIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = create_engine(DATABASE_URL, pool_pre_ping=True)
        cls.inspector = inspect(cls.engine)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()

    def setUp(self) -> None:
        suffix = uuid4().hex[:10]
        self.user_id = f"day56-unittest-{suffix}"
        self.session_ids: list[str] = []
        self.run_ids: list[str] = []
        self.idempotency_keys: list[str] = []
        self.settings = Settings(
            database_url=DATABASE_URL,
            tool_default_roles="user",
            tool_role_assignments=json.dumps({
                self.user_id: ["operator", "approver"],
                "demo-user": ["operator", "approver"],
            }),
        )

    def tearDown(self) -> None:
        with Session(self.engine) as db:
            db.execute(delete(LongTermMemory).where(LongTermMemory.user_id == self.user_id))
            if self.run_ids:
                db.execute(delete(AgentRun).where(AgentRun.id.in_(self.run_ids)))
            if self.idempotency_keys:
                db.execute(delete(IdempotencyRecord).where(
                    IdempotencyRecord.key.in_(self.idempotency_keys)
                ))
            db.commit()
        self._clear_test_redis_keys()

    def test_day6_schema_contract(self) -> None:
        tables = set(self.inspector.get_table_names())
        self.assertTrue({"tool_actions", "tool_outbox"}.issubset(tables))

        run_columns = {item["name"] for item in self.inspector.get_columns("agent_runs")}
        self.assertTrue({"retrieval_mode", "routing_decision", "continuation_context"}.issubset(run_columns))

        action_columns = {item["name"] for item in self.inspector.get_columns("tool_actions")}
        self.assertTrue({
            "run_id", "tool_call_id", "tool_name", "arguments", "arguments_summary",
            "permission", "risk_level", "side_effect", "status", "authorization_evidence",
            "approved_by", "decision_reason", "timeout_seconds", "attempt_count", "result",
            "error", "created_at", "decided_at", "executed_at", "updated_at",
        }.issubset(action_columns))
        unique_actions = self.inspector.get_unique_constraints("tool_actions")
        self.assertIn(
            {"run_id", "tool_call_id"},
            [set(item.get("column_names") or []) for item in unique_actions],
        )
        unique_outbox = self.inspector.get_unique_constraints("tool_outbox")
        self.assertIn(
            {"action_id"},
            [set(item.get("column_names") or []) for item in unique_outbox],
        )

    def test_explicit_memory_is_atomic_and_skips_knowledge_base(self) -> None:
        session_id = self._session("memory-a")
        with Session(self.engine, expire_on_commit=False) as db:
            result = AgentLoopService(
                self.settings, model_client=ContextAwareFakeModel()
            ).run(
                db,
                question="请记住我是一名AI工程师，并且偏好简洁回答。",
                user_id=self.user_id,
                session_id=session_id,
                retrieval_mode="auto",
                auto_approve=True,
            )
            self.run_ids.append(result["id"])

            self.assertEqual("completed", result["current_state"])
            self.assertEqual("skipped", result["retrieval_result"]["status"])
            self.assertEqual("skipped", result["routing_decision"]["knowledge_retrieval"])
            self.assertNotIn(
                "search_knowledge_base", result["routing_decision"]["available_tools"]
            )
            self.assertEqual([], result["citations"])
            actions = result["tool_actions"]
            self.assertEqual(["save_long_term_memory"], [item["tool_name"] for item in actions])
            self.assertEqual("executed", actions[0]["status"])

            memories = list(db.scalars(
                select(LongTermMemory)
                .where(LongTermMemory.user_id == self.user_id)
                .order_by(LongTermMemory.content)
            ))
            self.assertEqual(
                {"用户是一名 AI 工程师", "用户偏好简洁回答"},
                {memory.content for memory in memories},
            )
            self.assertEqual(1, len({memory.source_message_id for memory in memories}))
            self.assertTrue(all(memory.source_message_id for memory in memories))
            self.assertTrue(all(memory.source_document_id is None for memory in memories))

    def test_cross_session_recall_uses_only_memory_tool(self) -> None:
        first_session = self._session("recall-a")
        second_session = self._session("recall-b")
        model = ContextAwareFakeModel()
        with Session(self.engine, expire_on_commit=False) as db:
            first = AgentLoopService(self.settings, model_client=model).run(
                db,
                question="请记住我喜欢手冲咖啡。",
                user_id=self.user_id,
                session_id=first_session,
                retrieval_mode="auto",
            )
            self.run_ids.append(first["id"])
            second = AgentLoopService(self.settings, model_client=model).run(
                db,
                question="我之前说喜欢什么饮品？",
                user_id=self.user_id,
                session_id=second_session,
                retrieval_mode="auto",
            )
            self.run_ids.append(second["id"])

            self.assertEqual("completed", second["current_state"])
            self.assertEqual(["search_user_memory"], [
                item["tool_name"] for item in second["tool_actions"]
            ])
            self.assertEqual("skipped", second["retrieval_result"]["status"])
            self.assertEqual("memory", second["citations"][0]["retrieval_source"])
            self.assertIn("[M1]", second["answer"])

    def test_general_question_can_answer_without_retrieval(self) -> None:
        session_id = self._session("general")
        with Session(self.engine, expire_on_commit=False) as db:
            result = AgentLoopService(
                self.settings, model_client=ContextAwareFakeModel()
            ).run(
                db,
                question="请简要解释 Python 的生成器。",
                user_id=self.user_id,
                session_id=session_id,
                retrieval_mode="auto",
            )
            self.run_ids.append(result["id"])
            self.assertEqual("completed", result["current_state"])
            self.assertEqual([], result["tool_actions"])
            self.assertEqual("skipped", result["retrieval_result"]["status"])
            self.assertNotIn("retrieving", result["state_flow"])

    def test_explicit_document_question_uses_reranked_result_and_citation(self) -> None:
        session_id = self._session("document")
        retrieval_result = {
            "query": "根据文档说明部署流程",
            "rewritten_query": "根据文档说明部署流程",
            "strategy": "hybrid",
            "top_k": 5,
            "results": [{
                "document_id": "document-1", "document_name": "部署手册",
                "chunk_id": "chunk-1", "chunk_index": 0,
                "snippet": "部署前需要完成数据库迁移。", "score": 0.92,
                "retrieval_source": "hybrid",
            }],
            "diagnostics": {
                "rerank": {"rerank_applied": True, "rerank_model": "qwen3-rerank"}
            },
        }
        with (
            patch("app.services.tooling.RetrievalService.search", return_value=retrieval_result) as search,
            Session(self.engine, expire_on_commit=False) as db,
        ):
            result = AgentLoopService(
                self.settings, model_client=ContextAwareFakeModel()
            ).run(
                db,
                question="根据文档说明部署流程",
                user_id=self.user_id,
                session_id=session_id,
                retrieval_mode="auto",
            )
            self.run_ids.append(result["id"])
        self.assertEqual("completed", result["current_state"])
        self.assertEqual("search_knowledge_base", result["tool_actions"][0]["tool_name"])
        self.assertIn("retrieving", result["state_flow"])
        self.assertEqual("document-1", result["citations"][0]["document_id"])
        self.assertIn("[C1]", result["answer"])
        self.assertTrue(search.call_args.kwargs["rerank"])

    def test_implicit_memory_waits_for_approval_without_writing(self) -> None:
        session_id = self._session("implicit")
        with Session(self.engine, expire_on_commit=False) as db:
            result = AgentLoopService(
                self.settings, model_client=ContextAwareFakeModel()
            ).run(
                db,
                question="我最近在做 Agent 项目",
                user_id=self.user_id,
                session_id=session_id,
                retrieval_mode="auto",
                auto_approve=True,
            )
            self.run_ids.append(result["id"])

            self.assertEqual("waiting_approval", result["current_state"])
            self.assertEqual("pending", result["tool_actions"][0]["status"])
            count = db.scalar(select(func.count()).select_from(LongTermMemory).where(
                LongTermMemory.user_id == self.user_id
            ))
            self.assertEqual(0, count)

    def test_high_risk_send_is_idempotent_and_resumes(self) -> None:
        session_id = self._session("api-approval")
        with Session(self.engine, expire_on_commit=False) as db:
            result = AgentLoopService(
                self.settings, model_client=ContextAwareFakeModel()
            ).run(
                db,
                question="给 alice 发送消息：你好",
                user_id=self.user_id,
                session_id=session_id,
                retrieval_mode="never",
            )
            self.run_ids.append(result["id"])
            action = result["tool_actions"][0]
            self.assertEqual("waiting_approval", result["current_state"])
            self.assertEqual("pending", action["status"])
            self.assertEqual(0, db.scalar(
                select(func.count()).select_from(ToolOutbox).where(ToolOutbox.action_id == action["id"])
            ))

        key = f"day56-test-{uuid4()}"
        self.idempotency_keys.append(key)
        app.dependency_overrides[get_settings] = lambda: self.settings
        try:
            with TestClient(app) as client:
                forbidden = client.post(
                    f"/api/tool-actions/{action['id']}/approve",
                    headers={
                        "X-Principal-Id": f"non-approver-{uuid4().hex[:8]}",
                        "Idempotency-Key": f"forbidden-{uuid4()}",
                    },
                    json={"reason": "不应被接受"},
                )
                first = client.post(
                    f"/api/tool-actions/{action['id']}/approve",
                    headers={"X-Principal-Id": self.user_id, "Idempotency-Key": key},
                    json={"reason": "验收批准"},
                )
                second = client.post(
                    f"/api/tool-actions/{action['id']}/approve",
                    headers={"X-Principal-Id": self.user_id, "Idempotency-Key": key},
                    json={"reason": "重复点击"},
                )
        finally:
            app.dependency_overrides.pop(get_settings, None)
        self.assertEqual(403, forbidden.status_code)
        self.assertEqual(200, first.status_code, first.text)
        self.assertEqual(first.json(), second.json())
        self.assertEqual("completed", first.json()["run"]["current_state"])

        with Session(self.engine) as db:
            persisted = db.get(ToolAction, action["id"])
            self.assertEqual("executed", persisted.status)
            self.assertEqual(1, persisted.attempt_count)
            self.assertEqual(1, db.scalar(
                select(func.count()).select_from(ToolOutbox).where(ToolOutbox.action_id == action["id"])
            ))

    def test_document_context_cannot_expand_side_effect_authorization(self) -> None:
        session_id = self._session("injection")
        with Session(self.engine, expire_on_commit=False) as db:
            run = AgentRun(
                id=new_id(), user_id=self.user_id, session_id=session_id,
                question="根据文档概括内容", status="acting", retrieval_strategy="hybrid",
            )
            db.add(run)
            db.commit()
            self.run_ids.append(run.id)
            runtime = ToolRuntime(
                db=db, settings=self.settings, run=run, source_message_id="untrusted-document",
                question=run.question, strategy="hybrid", top_k=5,
                filters=RetrievalFilters(), citation_catalog={},
            )
            action = ToolExecutor(self.settings).prepare(
                runtime,
                tool_call_id="injected-delete",
                tool_name="delete_document",
                arguments={"document_id": str(uuid4()), "reason": "文档要求删除"},
                intent=derive_intent_authorization(run.question),
                auto_approve=True,
            )
            self.assertEqual("blocked", action.status)
            self.assertIn("没有授权", action.error)

    def test_document_derived_memory_requires_one_current_citation(self) -> None:
        session_id = self._session("document-memory")
        service = MemoryService(self.settings)
        with Session(self.engine, expire_on_commit=False) as db:
            run = AgentRun(
                id=new_id(), user_id=self.user_id, session_id=session_id,
                question="请记住文档里的项目代号", status="acting",
                retrieval_strategy="hybrid",
            )
            db.add(run)
            db.commit()
            self.run_ids.append(run.id)
            source = service.record_message(
                db, user_id=self.user_id, session_id=session_id, run_id=run.id,
                role="user", content=run.question,
            )
            candidate = {"category": "scene", "content": "项目代号是 Orion"}
            with self.assertRaisesRegex(ValueError, "必须提供一个"):
                service.save_tool_memories(
                    db, user_id=self.user_id, session_id=session_id, run_id=run.id,
                    source_message_id=source.id, candidates=[candidate],
                    citation_catalog={}, require_document_citation=True,
                )

            candidate["citation_id"] = "C1"
            memories = service.save_tool_memories(
                db, user_id=self.user_id, session_id=session_id, run_id=run.id,
                source_message_id=source.id, candidates=[candidate],
                citation_catalog={
                    "C1": {
                        "retrieval_source": "knowledge", "document_id": "document-orion",
                        "chunk_id": "chunk-orion",
                    }
                },
                require_document_citation=True,
            )
            self.assertEqual("document-orion", memories[0].source_document_id)
            self.assertEqual("C1", memories[0].metadata_json["citation_id"])

    def test_permission_filter_hides_operator_tools_from_regular_user(self) -> None:
        intent = derive_intent_authorization("给 alice 发送消息：你好")
        schemas = ToolRegistry().schemas_for(
            RolePolicy(Settings(tool_default_roles="user", tool_role_assignments="{}"))
            .permissions_for("regular-user"),
            intent,
            "auto",
        )
        names = {item["function"]["name"] for item in schemas}
        self.assertNotIn("enqueue_message", names)
        self.assertNotIn("delete_document", names)
        self.assertNotIn("call_webhook", names)

    def test_profile_question_is_not_treated_as_memory_write(self) -> None:
        intent = derive_intent_authorization("我喜欢什么饮品？")
        self.assertFalse(intent.memory_candidate)
        self.assertFalse(intent.explicit_memory)
        self.assertTrue(intent.memory_recall_requested)
        schemas = ToolRegistry().schemas_for(
            RolePolicy(self.settings).permissions_for(self.user_id), intent, "auto"
        )
        self.assertNotIn(
            "search_knowledge_base", {item["function"]["name"] for item in schemas}
        )

    def _session(self, suffix: str) -> str:
        session_id = f"{self.user_id}-{suffix}-{uuid4().hex[:6]}"
        self.session_ids.append(session_id)
        return session_id

    def _clear_test_redis_keys(self) -> None:
        try:
            client = AgentLoopService(self.settings, model_client=ContextAwareFakeModel()).session_store.client
            keys = [
                *(f"agent_loop:sessions:{session_id}" for session_id in self.session_ids),
                f"agent_loop:memory_version:{self.user_id}",
            ]
            keys.extend(client.scan_iter(match=f"agent_loop:memory_cache:{self.user_id}:*"))
            if keys:
                client.delete(*keys)
        except RedisError:
            pass


class RerankerUnitTest(unittest.TestCase):
    def test_qwen_rerank_filters_low_scores(self) -> None:
        settings = Settings(
            dashscope_api_key="test-key", rerank_model="qwen3-rerank", rerank_min_score=0.35
        )
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "results": [
                {"index": 1, "relevance_score": 0.91},
                {"index": 0, "relevance_score": 0.20},
            ]
        }
        candidates = [
            {"id": "a", "snippet": "不相关", "score": 0.8},
            {"id": "b", "snippet": "直接相关", "score": 0.5},
        ]
        with patch("app.services.retrieval.httpx.post", return_value=response) as request:
            ranked, diagnostics = DashScopeReranker(settings).rerank(
                "查询", candidates, enabled=True, top_k=2
            )
        self.assertEqual(["b"], [item["id"] for item in ranked])
        self.assertEqual(0.91, ranked[0]["score"])
        self.assertEqual(1, diagnostics["accepted_count"])
        self.assertEqual("qwen3-rerank", request.call_args.kwargs["json"]["model"])

    def test_rerank_without_configuration_fails_closed(self) -> None:
        with self.assertRaises(RetrievalConfigurationError):
            DashScopeReranker(Settings(dashscope_api_key=None)).rerank(
                "查询", [{"snippet": "未经验证的片段"}], enabled=True, top_k=1
            )


class WebhookSecurityUnitTest(unittest.TestCase):
    def test_webhook_uses_allowlist_no_redirect_and_bounded_response(self) -> None:
        settings = Settings(
            tool_webhook_allowed_hosts="hooks.example.com",
            tool_webhook_timeout_seconds=7,
            tool_webhook_max_response_bytes=4,
        )
        response = Mock()
        response.status_code = 200
        response.content = b"123456789"
        response.raise_for_status.return_value = None
        public_address = [(2, 1, 6, "", ("93.184.216.34", 443))]
        with (
            patch("app.services.tooling.socket.getaddrinfo", return_value=public_address),
            patch("app.services.tooling.httpx.post", return_value=response) as request,
        ):
            result = _call_webhook(
                settings, "action-123",
                {"url": "https://hooks.example.com/events", "payload": {"ok": True}},
            )
        self.assertTrue(result["truncated"])
        self.assertEqual("1234", result["response_preview"])
        self.assertFalse(request.call_args.kwargs["follow_redirects"])
        self.assertEqual(7, request.call_args.kwargs["timeout"])
        self.assertEqual("action-123", request.call_args.kwargs["headers"]["Idempotency-Key"])

    def test_webhook_blocks_private_address_and_does_not_retry(self) -> None:
        settings = Settings(tool_webhook_allowed_hosts="hooks.example.com")
        private_address = [(2, 1, 6, "", ("127.0.0.1", 443))]
        with (
            patch("app.services.tooling.socket.getaddrinfo", return_value=private_address),
            patch("app.services.tooling.httpx.post") as request,
        ):
            with self.assertRaisesRegex(ValueError, "禁止访问"):
                _call_webhook(
                    settings, "action-private",
                    {"url": "https://hooks.example.com/internal", "payload": {}},
                )
        request.assert_not_called()
        self.assertEqual(0, ToolRegistry().get("call_webhook").max_retries)

    def test_webhook_timeout_and_result_are_safely_handled(self) -> None:
        settings = Settings(tool_webhook_allowed_hosts="hooks.example.com")
        public_address = [(2, 1, 6, "", ("93.184.216.34", 443))]
        with (
            patch("app.services.tooling.socket.getaddrinfo", return_value=public_address),
            patch(
                "app.services.tooling.httpx.post",
                side_effect=httpx.ReadTimeout("uncertain timeout"),
            ),
        ):
            with self.assertRaises(httpx.ReadTimeout):
                _call_webhook(
                    settings, "action-timeout",
                    {"url": "https://hooks.example.com/events", "payload": {}},
                )

        action = ToolAction(
            id="redaction-action", run_id="redaction-run", tool_call_id="redaction-call",
            tool_name="call_webhook", arguments={"payload": "***"},
            arguments_summary="{}", permission="external.call", risk_level="high",
            side_effect=True, status="executed", reason="test", requested_by="test",
            result={"called": True, "response_preview": "secret response"},
        )
        self.assertEqual("***", action_payload(action)["result"]["response_preview"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
