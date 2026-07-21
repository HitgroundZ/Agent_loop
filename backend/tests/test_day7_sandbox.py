from __future__ import annotations

import json
import os
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

import httpx
from redis.exceptions import RedisError
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AgentRun
from app.services.agent_loop import AgentLoopService
from app.services.agent_model import ModelToolCall, ModelTurn
from app.services.sandbox import SandboxClient, SandboxPolicyRejected
from app.services.tooling import RolePolicy, ToolRegistry, derive_intent_authorization


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://agent_loop:agent_loop@127.0.0.1:5432/agent_loop",
)


class SandboxCallingModel:
    def __init__(self, arguments: dict | None = None) -> None:
        self.arguments = arguments or {
            "argv": ["python", "-c", "print(6*7)"],
            "env": {},
        }

    def complete(self, messages: list[dict], *, tools=None, tool_choice=None) -> ModelTurn:
        if messages and messages[-1].get("role") == "tool":
            payload = json.loads(messages[-1].get("content") or "{}")
            result = payload.get("result") or {}
            content = f"沙箱结果：{str(result.get('stdout') or '').strip()}"
            return ModelTurn(
                content=content,
                tool_calls=[],
                token_usage=_tokens(),
                raw_assistant_message={"role": "assistant", "content": content},
            )
        call = ModelToolCall(
            id=f"sandbox-call-{uuid4().hex[:8]}",
            name="execute_sandbox_command",
            arguments=self.arguments,
        )
        return ModelTurn(
            content="",
            tool_calls=[call],
            token_usage=_tokens(),
            raw_assistant_message={
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                }],
            },
        )


class Day7SandboxAgentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = create_engine(DATABASE_URL, pool_pre_ping=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()

    def setUp(self) -> None:
        suffix = uuid4().hex[:10]
        self.user_id = f"day7-unittest-{suffix}"
        self.session_ids: list[str] = []
        self.run_ids: list[str] = []
        self.settings = Settings(
            database_url=DATABASE_URL,
            tool_default_roles="user",
            tool_role_assignments=json.dumps({self.user_id: ["operator", "approver"]}),
        )

    def tearDown(self) -> None:
        with Session(self.engine) as db:
            if self.run_ids:
                db.execute(delete(AgentRun).where(AgentRun.id.in_(self.run_ids)))
                db.commit()
        try:
            store = AgentLoopService(self.settings, model_client=SandboxCallingModel()).session_store
            keys = [f"agent_loop:sessions:{session_id}" for session_id in self.session_ids]
            if keys:
                store.client.delete(*keys)
        except RedisError:
            pass

    def test_safe_command_logs_to_action_and_trace(self) -> None:
        sandbox_result = _sandbox_result(stdout="42\n")
        with patch("app.services.tooling.SandboxClient.execute", return_value=sandbox_result):
            result = self._run(SandboxCallingModel())

        action = result["tool_actions"][0]
        self.assertEqual("executed", action["status"])
        self.assertEqual("42\n", action["result"]["stdout"])
        acting_events = [event for event in result["trace_events"] if event["state"] == "acting"]
        self.assertTrue(acting_events)
        traced_actions = acting_events[-1]["output"].get("actions") or []
        self.assertEqual("42\n", traced_actions[0]["result"]["stdout"])
        self.assertIn("42", result["answer"])

    def test_service_policy_rejection_becomes_blocked_action(self) -> None:
        rejected = SandboxPolicyRejected({
            "code": "command_denied",
            "message": "命令 rm 位于拒绝列表",
            "rule": "denylist",
        })
        model = SandboxCallingModel({"argv": ["rm", "-rf", "/"], "env": {}})
        with patch("app.services.tooling.SandboxClient.execute", side_effect=rejected):
            result = self._run(model, question="请执行命令 rm -rf /，用于验证拒绝策略")

        action = result["tool_actions"][0]
        self.assertEqual("blocked", action["status"])
        self.assertEqual("denied", action["result"]["policy"]["decision"])
        self.assertEqual("command_denied", action["result"]["policy"]["code"])

    def test_sensitive_environment_is_blocked_before_service_call(self) -> None:
        model = SandboxCallingModel({
            "argv": ["echo", "ok"],
            "env": {"DASHSCOPE_API_KEY": "must-not-persist"},
        })
        with patch("app.services.tooling.SandboxClient.execute") as execute:
            result = self._run(model)

        execute.assert_not_called()
        action = result["tool_actions"][0]
        self.assertEqual("blocked", action["status"])
        self.assertEqual("***", action["arguments"]["env"])
        self.assertNotIn("must-not-persist", json.dumps(action, ensure_ascii=False))

    def test_tool_visibility_requires_operator_and_explicit_intent(self) -> None:
        intent = derive_intent_authorization("请在 Docker 沙箱执行命令 argv: [\"echo\",\"ok\"]")
        regular = ToolRegistry().schemas_for({"memory.read"}, intent, "auto")
        self.assertNotIn("execute_sandbox_command", _tool_names(regular))
        operator_permissions = RolePolicy(self.settings).permissions_for(self.user_id)
        operator = ToolRegistry().schemas_for(operator_permissions, intent, "auto")
        self.assertIn("execute_sandbox_command", _tool_names(operator))
        no_intent = ToolRegistry().schemas_for(
            operator_permissions, derive_intent_authorization("普通聊天"), "auto"
        )
        self.assertNotIn("execute_sandbox_command", _tool_names(no_intent))

    def _run(self, model: SandboxCallingModel, *, question: str | None = None) -> dict:
        session_id = f"{self.user_id}-{uuid4().hex[:8]}"
        self.session_ids.append(session_id)
        with Session(self.engine) as db:
            result = AgentLoopService(self.settings, model_client=model).run(
                db,
                question=question or "请使用 Python 在 Docker 沙箱执行命令并计算 6*7",
                user_id=self.user_id,
                session_id=session_id,
                retrieval_mode="never",
                auto_approve=True,
            )
            self.run_ids.append(result["id"])
            return result


class SandboxClientTest(unittest.TestCase):
    def test_client_sends_only_contract_and_maps_policy_rejection(self) -> None:
        settings = Settings(
            sandbox_service_url="http://sandbox-service:8080",
            sandbox_service_token="internal-token",
        )
        ok = httpx.Response(
            200,
            json=_sandbox_result(stdout="ok\n"),
            request=httpx.Request("POST", "http://sandbox-service:8080/v1/executions"),
        )
        with patch("app.services.sandbox.httpx.post", return_value=ok) as post:
            result = SandboxClient(settings).execute(
                execution_id="action-1", argv=["echo", "ok"], env={}
            )
        self.assertEqual("ok\n", result["stdout"])
        self.assertEqual("Bearer internal-token", post.call_args.kwargs["headers"]["Authorization"])
        self.assertEqual(
            {"execution_id": "action-1", "argv": ["echo", "ok"], "env": {}},
            post.call_args.kwargs["json"],
        )

        denied = httpx.Response(
            403,
            json={"detail": {"code": "command_denied", "message": "denied", "rule": "denylist"}},
            request=httpx.Request("POST", "http://sandbox-service:8080/v1/executions"),
        )
        with patch("app.services.sandbox.httpx.post", return_value=denied):
            with self.assertRaises(SandboxPolicyRejected):
                SandboxClient(settings).execute(
                    execution_id="action-2", argv=["rm", "-rf", "/"], env={}
                )


def _sandbox_result(*, stdout: str) -> dict:
    return {
        "execution_id": "action",
        "execution_status": "succeeded",
        "exit_code": 0,
        "timed_out": False,
        "stdout": stdout,
        "stderr": "",
        "stdout_bytes": len(stdout.encode()),
        "stderr_bytes": 0,
        "stdout_sha256": "hash",
        "stderr_sha256": "hash",
        "stdout_truncated": False,
        "stderr_truncated": False,
        "duration_ms": 20,
        "container_id": "abc123",
        "image": "python:3.13-slim",
        "policy": {"decision": "allowed", "rule": "python-runtime"},
        "limits": {"timeout_seconds": 5, "memory": "128m", "nano_cpus": 500000000, "pids": 64, "network": "none"},
        "cleanup": {"container_removed": True, "temporary_filesystems_removed": True},
    }


def _tokens() -> dict:
    return {"input_tokens": 4, "output_tokens": 4, "total_tokens": 8, "estimated": False}


def _tool_names(schemas: list[dict]) -> set[str]:
    return {item["function"]["name"] for item in schemas}


if __name__ == "__main__":
    unittest.main()
