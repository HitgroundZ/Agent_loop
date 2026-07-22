from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatchcase
import ipaddress
import json
import re
import socket
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AgentRun, ToolAction, ToolOutbox, new_id
from app.services.documents import delete_document_resource
from app.services.memory import MemoryService
from app.services.retrieval import RetrievalFilters, RetrievalService
from app.services.sandbox import SandboxClient, SandboxPolicyRejected


RiskLevel = Literal["low", "medium", "high"]

ROLE_PERMISSIONS = {
    "user": {"knowledge.read", "memory.read", "memory.write"},
    "operator": {
        "knowledge.read", "memory.read", "memory.write",
        "document.delete", "message.send", "external.call", "sandbox.execute",
    },
    "approver": {"approval.read", "approval.decide"},
}


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict
    permission: str
    risk_level: RiskLevel
    side_effect: bool
    timeout_seconds: int
    max_retries: int
    sensitive_fields: tuple[str, ...] = ()
    sensitive_result_fields: tuple[str, ...] = ()
    allowed_authorization_sources: tuple[str, ...] = ("user_message", "server_policy")

    def llm_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


@dataclass(frozen=True)
class IntentAuthorization:
    question: str
    explicit_memory: bool
    memory_candidate: bool
    memory_recall_requested: bool
    document_context_requested: bool
    block_knowledge_retrieval: bool
    allowed_side_effect_tools: frozenset[str]
    evidence: dict[str, str]

    def allows(self, tool_name: str) -> bool:
        if tool_name == "save_long_term_memory":
            return self.memory_candidate
        return tool_name in self.allowed_side_effect_tools

    def payload(self) -> dict:
        return {
            "explicit_memory": self.explicit_memory,
            "memory_candidate": self.memory_candidate,
            "memory_recall_requested": self.memory_recall_requested,
            "document_context_requested": self.document_context_requested,
            "block_knowledge_retrieval": self.block_knowledge_retrieval,
            "allowed_side_effect_tools": sorted(self.allowed_side_effect_tools),
            "evidence": self.evidence,
        }


@dataclass
class ToolRuntime:
    db: Session
    settings: Settings
    run: AgentRun
    source_message_id: str
    question: str
    strategy: str
    top_k: int | None
    filters: RetrievalFilters
    citation_catalog: dict[str, dict]


class RolePolicy:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        try:
            parsed = json.loads(settings.tool_role_assignments or "{}")
        except json.JSONDecodeError:
            parsed = {}
        self.assignments = parsed if isinstance(parsed, dict) else {}

    def roles_for(self, principal_id: str) -> set[str]:
        roles = set(self.settings.tool_default_role_list)
        for subject_pattern, assigned in self.assignments.items():
            if not isinstance(subject_pattern, str) or not fnmatchcase(principal_id, subject_pattern):
                continue
            if isinstance(assigned, str):
                assigned = [assigned]
            if isinstance(assigned, list):
                roles.update(str(role) for role in assigned)
        return roles

    def permissions_for(self, principal_id: str) -> set[str]:
        permissions: set[str] = set()
        for role in self.roles_for(principal_id):
            permissions.update(ROLE_PERMISSIONS.get(role, set()))
        return permissions

    def require(self, principal_id: str, permission: str) -> None:
        if permission not in self.permissions_for(principal_id):
            raise PermissionError(f"主体 {principal_id} 缺少权限 {permission}")


class ToolRegistry:
    def __init__(self) -> None:
        self._definitions = {item.name: item for item in _tool_definitions()}

    def get(self, name: str) -> ToolDefinition | None:
        return self._definitions.get(name)

    def schemas_for(
        self,
        permissions: set[str],
        intent: IntentAuthorization,
        retrieval_mode: str,
    ) -> list[dict]:
        schemas: list[dict] = []
        for definition in self._definitions.values():
            if definition.permission not in permissions:
                continue
            if definition.name == "search_knowledge_base" and retrieval_mode == "never":
                continue
            if (
                definition.name == "search_knowledge_base"
                and retrieval_mode == "auto"
                and intent.block_knowledge_retrieval
            ):
                continue
            if definition.side_effect and not intent.allows(definition.name):
                continue
            schemas.append(definition.llm_schema())
        return schemas


class ToolExecutor:
    def __init__(self, settings: Settings, registry: ToolRegistry | None = None) -> None:
        self.settings = settings
        self.registry = registry or ToolRegistry()
        self.roles = RolePolicy(settings)

    def prepare(
        self,
        runtime: ToolRuntime,
        *,
        tool_call_id: str,
        tool_name: str,
        arguments: dict,
        intent: IntentAuthorization,
        auto_approve: bool,
    ) -> ToolAction:
        definition = self.registry.get(tool_name)
        if definition is None:
            return self._blocked_unknown(runtime, tool_call_id, tool_name, arguments)
        arguments = dict(arguments or {})
        if tool_name == "enqueue_message":
            # 本阶段不允许模型选择投递渠道；只写通用 Outbox，由后续 worker 绑定真实渠道。
            arguments["channel"] = "generic"
        execution_arguments = _safe_execution_arguments(self.settings, tool_name, arguments)

        action = ToolAction(
            id=new_id(),
            run_id=runtime.run.id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments=_redact(arguments, definition.sensitive_fields),
            arguments_summary=_argument_summary(arguments, definition.sensitive_fields),
            execution_context={
                "user_id": runtime.run.user_id,
                "session_id": runtime.run.session_id,
                "source_message_id": runtime.source_message_id,
                "question": runtime.question,
                "strategy": runtime.strategy,
                "top_k": runtime.top_k,
                "filters": _filters_payload(runtime.filters),
                "citation_catalog": runtime.citation_catalog,
                "raw_arguments": execution_arguments,
            },
            permission=definition.permission,
            risk_level=definition.risk_level,
            side_effect=definition.side_effect,
            status="proposed",
            reason=_risk_reason(definition),
            authorization_source="user_message",
            authorization_evidence=intent.evidence.get(tool_name, ""),
            requested_by=runtime.run.user_id,
            timeout_seconds=definition.timeout_seconds,
            max_retries=definition.max_retries,
        )
        runtime.db.add(action)
        runtime.db.flush()

        try:
            if action.authorization_source not in definition.allowed_authorization_sources:
                raise PolicyBlocked("工具授权来源不在注册表允许范围内")
            _validate_arguments(definition, arguments)
            if tool_name == "execute_sandbox_command":
                _validate_sandbox_arguments(self.settings, arguments)
            self.roles.require(runtime.run.user_id, definition.permission)
            if (
                tool_name == "search_knowledge_base"
                and runtime.run.retrieval_mode == "auto"
                and intent.block_knowledge_retrieval
            ):
                raise PolicyBlocked("纯记忆表达不允许扩张为知识库检索")
            if definition.side_effect:
                if not intent.allows(tool_name):
                    raise PolicyBlocked("原始用户消息没有授权该副作用工具")
                _validate_grounded_target(tool_name, arguments, runtime.question)
        except (ValueError, PermissionError, PolicyBlocked) as exc:
            action.status = "blocked"
            action.error = str(exc)
            action.result = {"blocked": True, "reason": str(exc)}
            action.executed_at = _now()
            runtime.db.commit()
            runtime.db.refresh(action)
            return action

        if tool_name == "execute_sandbox_command":
            should_wait = not auto_approve
        else:
            should_wait = definition.risk_level == "high" or (
                definition.risk_level == "medium"
                and (not intent.explicit_memory or not auto_approve)
            )
        if should_wait:
            action.status = "pending"
            runtime.db.commit()
            runtime.db.refresh(action)
            return action

        runtime.db.commit()
        return self.execute(runtime.db, action, runtime=runtime)

    def execute(
        self,
        db: Session,
        action: ToolAction,
        *,
        runtime: ToolRuntime | None = None,
    ) -> ToolAction:
        definition = self.registry.get(action.tool_name)
        if definition is None:
            action.status = "failed"
            action.error = "工具未注册"
            db.commit()
            return action
        if action.status == "executed":
            return action
        if action.status in {"rejected", "failed", "blocked"}:
            return action

        if runtime is None:
            runtime = _runtime_from_action(db, self.settings, action)
        while True:
            action.status = "running"
            action.attempt_count = int(action.attempt_count or 0) + 1
            db.add(action)
            db.commit()
            db.refresh(action)
            try:
                result = self._dispatch(runtime, action)
            except SandboxPolicyRejected as exc:
                db.rollback()
                action = db.get(ToolAction, action.id) or action
                action.status = "blocked"
                action.error = str(exc)
                action.result = {
                    "blocked": True,
                    "policy": {"decision": "denied", **exc.payload},
                }
                action.executed_at = _now()
                db.add(action)
                db.commit()
                db.refresh(action)
                return action
            except Exception as exc:
                db.rollback()
                action = db.get(ToolAction, action.id) or action
                action.error = str(exc)
                if action.attempt_count <= definition.max_retries:
                    db.add(action)
                    db.commit()
                    continue
                action.status = "failed"
                action.executed_at = _now()
            else:
                action.result = result
                action.status = "executed"
                action.error = None
                action.executed_at = _now()
            db.add(action)
            db.commit()
            db.refresh(action)
            return action

    def _dispatch(self, runtime: ToolRuntime, action: ToolAction) -> dict:
        arguments = (action.execution_context or {}).get("raw_arguments") or action.arguments or {}
        if action.tool_name == "search_user_memory":
            return MemoryService(runtime.settings).search(
                runtime.db,
                user_id=runtime.run.user_id,
                session_id=runtime.run.session_id,
                query=str(arguments.get("query") or runtime.question),
            )
        if action.tool_name == "search_knowledge_base":
            return RetrievalService(runtime.settings).search(
                runtime.db,
                query=str(arguments.get("query") or runtime.question),
                strategy=runtime.strategy,
                top_k=runtime.top_k,
                filters=runtime.filters,
                rerank=True,
            )
        if action.tool_name == "save_long_term_memory":
            candidates = list(arguments.get("memories") or [])
            deterministic = extract_memory_candidates(runtime.question)
            document_derived = _has_document_cue(runtime.question)
            if deterministic and not document_derived:
                candidates = deterministic
            memories = MemoryService(runtime.settings).save_tool_memories(
                runtime.db,
                user_id=runtime.run.user_id,
                session_id=runtime.run.session_id,
                run_id=runtime.run.id,
                source_message_id=runtime.source_message_id,
                candidates=candidates,
                citation_catalog=runtime.citation_catalog,
                require_document_citation=document_derived,
            )
            return {
                "saved": True,
                "memory_count": len(memories),
                "memory_ids": [memory.id for memory in memories],
            }
        if action.tool_name == "delete_document":
            return delete_document_resource(
                runtime.db,
                runtime.settings,
                str(arguments.get("document_id") or ""),
            )
        if action.tool_name == "enqueue_message":
            existing = runtime.db.scalar(
                select(ToolOutbox).where(ToolOutbox.action_id == action.id)
            )
            if existing is None:
                existing = ToolOutbox(
                    id=new_id(),
                    action_id=action.id,
                    channel=str(arguments.get("channel") or "generic"),
                    recipient=str(arguments.get("recipient") or ""),
                    payload={"content": str(arguments.get("content") or "")},
                    status="queued",
                )
                runtime.db.add(existing)
                runtime.db.commit()
                runtime.db.refresh(existing)
            return {"queued": True, "outbox_id": existing.id, "status": existing.status}
        if action.tool_name == "call_webhook":
            return _call_webhook(runtime.settings, action.id, arguments)
        if action.tool_name == "execute_sandbox_command":
            return SandboxClient(runtime.settings).execute(
                execution_id=action.id,
                argv=list(arguments.get("argv") or []),
                env=dict(arguments.get("env") or {}),
            )
        raise ValueError(f"工具未实现：{action.tool_name}")

    def _blocked_unknown(
        self,
        runtime: ToolRuntime,
        tool_call_id: str,
        tool_name: str,
        arguments: dict,
    ) -> ToolAction:
        action = ToolAction(
            id=new_id(), run_id=runtime.run.id, tool_call_id=tool_call_id,
            tool_name=tool_name, arguments=arguments, arguments_summary="未注册工具",
            permission="unknown", risk_level="high", side_effect=True, status="blocked",
            reason="模型请求了未注册工具", authorization_source="model",
            authorization_evidence="", requested_by=runtime.run.user_id,
            result={"blocked": True, "reason": "工具未注册"}, error="工具未注册",
            executed_at=_now(),
        )
        runtime.db.add(action)
        runtime.db.commit()
        runtime.db.refresh(action)
        return action


class PolicyBlocked(RuntimeError):
    pass


def derive_intent_authorization(question: str) -> IntentAuthorization:
    normalized = re.sub(r"\s+", " ", question or "").strip()
    lowered = normalized.lower()
    explicit_memory = any(marker in lowered for marker in ("请记住", "记住我", "remember that", "remember my"))
    looks_like_question = normalized.endswith(("?", "？")) or any(
        marker in lowered
        for marker in ("什么", "是否", "吗", "么", "how", "what", "which", "do i", "am i")
    )
    profile_marker = any(
        marker in lowered
        for marker in (
            "我叫", "我的名字", "我是", "我喜欢", "我偏好", "我习惯", "我不喜欢",
            "我的职业", "我最近在做", "i am", "i like", "i prefer", "my name",
        )
    )
    memory_candidate = explicit_memory or (profile_marker and not looks_like_question)
    document_cue = _has_document_cue(normalized)
    memory_recall = any(
        marker in lowered
        for marker in (
            "我之前", "我的偏好", "我的职业", "我喜欢什么", "还记得",
            "remember about me", "what do i like", "my preference",
        )
    )
    allowed: set[str] = set()
    evidence: dict[str, str] = {}
    if memory_candidate:
        evidence["save_long_term_memory"] = _shorten(normalized, 240)
    if any(marker in lowered for marker in ("删除", "移除", "delete", "remove")):
        allowed.add("delete_document")
        evidence["delete_document"] = _shorten(normalized, 240)
    if any(marker in lowered for marker in ("发送", "通知", "发消息", "send", "notify")):
        allowed.add("enqueue_message")
        evidence["enqueue_message"] = _shorten(normalized, 240)
    if any(marker in lowered for marker in ("webhook", "回调", "调用接口", "http://", "https://")):
        allowed.add("call_webhook")
        evidence["call_webhook"] = _shorten(normalized, 240)
    if any(
        marker in lowered
        for marker in (
            "执行命令", "运行命令", "沙箱执行", "docker 沙箱", "docker sandbox",
            "run command", "execute command", "用 python", "使用 python", "python 计算",
            "运行 python", "执行 python", "run python", "argv:", "argv：",
        )
    ):
        allowed.add("execute_sandbox_command")
        evidence["execute_sandbox_command"] = _shorten(normalized, 240)
    return IntentAuthorization(
        question=normalized,
        explicit_memory=explicit_memory,
        memory_candidate=memory_candidate,
        memory_recall_requested=memory_recall,
        document_context_requested=document_cue,
        block_knowledge_retrieval=(memory_candidate or memory_recall) and not document_cue,
        allowed_side_effect_tools=frozenset(allowed),
        evidence=evidence,
    )


def _has_document_cue(value: str) -> bool:
    lowered = (value or "").lower()
    return any(
        marker in lowered
        for marker in (
            "根据文档", "知识库", "资料中", "文件中", "文档里", "文档", "文件",
            "according to the document",
        )
    )


def extract_memory_candidates(question: str) -> list[dict]:
    normalized = re.sub(r"\s+", " ", question or "").strip().rstrip("。.!！")
    value = re.sub(r"^(请记住|请帮我记住|记住)", "", normalized).strip()
    candidates: list[dict] = []
    occupation = re.search(r"我(?:是一名|是一个|是)\s*([^，,；;并且]+)", value, re.I)
    if occupation:
        content = occupation.group(1).strip()
        content = re.sub(r"([A-Za-z0-9])([\u4e00-\u9fff])", r"\1 \2", content)
        if content:
            candidates.append({
                "category": "user_profile",
                "content": f"用户是一名 {content}",
                "reason": "用户明确表达职业或身份",
            })
    preference = re.search(r"(?:我)?偏好\s*([^，,；;并且]+)", value, re.I)
    if preference:
        content = preference.group(1).strip()
        if content:
            candidates.append({
                "category": "user_profile",
                "content": f"用户偏好{content}",
                "reason": "用户明确表达偏好",
            })
    like = re.search(r"我喜欢\s*([^，,；;并且]+)", value, re.I)
    if like:
        content = like.group(1).strip()
        if content:
            candidates.append({
                "category": "user_profile", "content": f"用户喜欢{content}",
                "reason": "用户明确表达喜好",
            })
    recent = re.search(r"我最近在做\s*(.+)$", value, re.I)
    if recent:
        candidates.append({
            "category": "scene", "content": f"用户最近在做{recent.group(1).strip()}",
            "reason": "可能影响后续对话的当前场景",
        })
    if not candidates and value:
        candidates.append({
            "category": "user_profile", "content": f"用户明确表达：{value}",
            "reason": "用户要求长期记住该信息",
        })
    return candidates


def action_payload(action: ToolAction) -> dict:
    definition = ToolRegistry().get(action.tool_name)
    visible_result = action.result or {}
    if definition and definition.sensitive_result_fields:
        visible_result = _redact(visible_result, definition.sensitive_result_fields)
    return {
        "id": action.id,
        "run_id": action.run_id,
        "tool_call_id": action.tool_call_id,
        "tool_name": action.tool_name,
        "arguments": action.arguments or {},
        "arguments_summary": action.arguments_summary,
        "permission": action.permission,
        "risk_level": action.risk_level,
        "side_effect": action.side_effect,
        "status": action.status,
        "reason": action.reason,
        "authorization_source": action.authorization_source,
        "authorization_evidence": action.authorization_evidence,
        "requested_by": action.requested_by,
        "approved_by": action.approved_by,
        "decision_reason": action.decision_reason,
        "result": visible_result,
        "error": action.error,
        "timeout_seconds": action.timeout_seconds,
        "max_retries": action.max_retries,
        "attempt_count": action.attempt_count,
        "created_at": action.created_at.isoformat() if action.created_at else None,
        "decided_at": action.decided_at.isoformat() if action.decided_at else None,
        "executed_at": action.executed_at.isoformat() if action.executed_at else None,
        "updated_at": action.updated_at.isoformat() if action.updated_at else None,
    }


def _tool_definitions() -> list[ToolDefinition]:
    obj = {"type": "object", "additionalProperties": False}
    return [
        ToolDefinition(
            "search_user_memory", "检索当前用户跨会话的长期记忆。",
            {**obj, "properties": {"query": {"type": "string"}}, "required": ["query"]},
            "memory.read", "low", False, 5, 1,
        ),
        ToolDefinition(
            "search_knowledge_base", "仅当问题需要项目知识库或指定文档信息时检索文档。",
            {**obj, "properties": {"query": {"type": "string"}}, "required": ["query"]},
            "knowledge.read", "low", False, 20, 0,
        ),
        ToolDefinition(
            "save_long_term_memory", "保存用户稳定身份、偏好或长期场景；每条必须是原子事实。",
            {
                **obj,
                "properties": {
                    "memories": {
                        "type": "array", "minItems": 1,
                        "items": {
                            "type": "object", "additionalProperties": False,
                            "properties": {
                                "category": {"type": "string", "enum": ["user_profile", "scene", "event_summary"]},
                                "content": {"type": "string"},
                                "reason": {"type": "string"},
                                "citation_id": {"type": "string"},
                            },
                            "required": ["category", "content"],
                        },
                    }
                },
                "required": ["memories"],
            },
            "memory.write", "medium", True, 5, 0,
        ),
        ToolDefinition(
            "delete_document", "删除指定 document_id 对应的知识库文档。",
            {**obj, "properties": {"document_id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["document_id", "reason"]},
            "document.delete", "high", True, 15, 0,
        ),
        ToolDefinition(
            "enqueue_message", "将待发送消息写入可靠 Outbox。",
            {
                **obj,
                "properties": {
                    "channel": {
                        "type": "string", "enum": ["generic"],
                        "description": "当前阶段固定为 generic，真实渠道由后续 worker 决定。",
                    },
                    "recipient": {"type": "string", "description": "原始用户消息中明确给出的收件人。"},
                    "content": {"type": "string", "description": "用户要求发送的消息正文。"},
                    "reason": {"type": "string", "description": "提出该 action 的理由。"},
                },
                "required": ["channel", "recipient", "content", "reason"],
            },
            "message.send", "high", True, 5, 0, ("content",),
        ),
        ToolDefinition(
            "call_webhook", "向服务端白名单中的 HTTPS Webhook 发送 JSON。",
            {**obj, "properties": {"url": {"type": "string"}, "payload": {"type": "object"}, "reason": {"type": "string"}}, "required": ["url", "payload", "reason"]},
            "external.call", "high", True, 10, 0,
            sensitive_fields=("payload",), sensitive_result_fields=("response_preview",),
        ),
        ToolDefinition(
            "execute_sandbox_command",
            "在无网络、临时文件系统和严格资源限制的一次性 Docker 容器中执行结构化命令。不得使用 shell 字符串。",
            {
                **obj,
                "properties": {
                    "argv": {
                        "type": "array", "minItems": 1, "maxItems": 64,
                        "items": {"type": "string", "maxLength": 4096},
                        "description": "可执行文件与参数数组，例如 ['python','-c','print(6*7)']。",
                    },
                    "env": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                        "description": "可选安全环境变量；API key、token、secret 一律禁止。",
                    },
                },
                "required": ["argv"],
            },
            "sandbox.execute", "medium", True, 10, 0,
            sensitive_fields=("env",),
        ),
    ]


def _validate_arguments(definition: ToolDefinition, arguments: dict) -> None:
    _validate_schema_value(arguments, definition.input_schema, path=definition.name)


def _validate_schema_value(value: Any, schema: dict, *, path: str) -> None:
    expected = schema.get("type")
    valid_type = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
    }.get(expected, True)
    if not valid_type:
        raise ValueError(f"工具参数 {path} 类型必须是 {expected}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"工具参数 {path} 不在允许值范围内")

    if expected == "object":
        properties = schema.get("properties") or {}
        for field in schema.get("required") or []:
            if field not in value or value[field] in (None, "", []):
                raise ValueError(f"工具参数缺少 {path}.{field}")
        if schema.get("additionalProperties") is False:
            unexpected = set(value) - set(properties)
            if unexpected:
                raise ValueError(f"工具参数 {path} 包含未知字段：{', '.join(sorted(unexpected))}")
        for field, item in value.items():
            if field in properties:
                _validate_schema_value(item, properties[field], path=f"{path}.{field}")
    elif expected == "array":
        if len(value) < int(schema.get("minItems") or 0):
            raise ValueError(f"工具参数 {path} 数量不足")
        if schema.get("maxItems") is not None and len(value) > int(schema["maxItems"]):
            raise ValueError(f"工具参数 {path} 数量超过上限")
        item_schema = schema.get("items") or {}
        for index, item in enumerate(value):
            _validate_schema_value(item, item_schema, path=f"{path}[{index}]")
    elif expected == "string" and schema.get("maxLength") is not None:
        if len(value) > int(schema["maxLength"]):
            raise ValueError(f"工具参数 {path} 长度超过上限")


def _validate_grounded_target(tool_name: str, arguments: dict, question: str) -> None:
    targets = {
        "delete_document": str(arguments.get("document_id") or ""),
        "enqueue_message": str(arguments.get("recipient") or ""),
        "call_webhook": str(arguments.get("url") or ""),
    }
    target = targets.get(tool_name)
    if target and target not in question:
        raise PolicyBlocked("副作用工具目标并非来自原始用户消息")
    if tool_name == "enqueue_message":
        content = str(arguments.get("content") or "")
        if content and content != question and content not in question:
            raise PolicyBlocked("发送内容并非来自原始用户消息")


def _validate_sandbox_arguments(settings: Settings, arguments: dict) -> None:
    env = arguments.get("env") or {}
    if not isinstance(env, dict):
        raise ValueError("沙箱 env 必须是字符串映射")
    sensitive = re.compile(r"(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH|COOKIE)", re.I)
    for key, value in env.items():
        normalized = str(key).upper()
        if sensitive.search(normalized):
            raise PolicyBlocked(f"环境变量 {key} 可能包含凭证，禁止注入沙箱")
        if normalized not in settings.sandbox_allowed_env_key_set:
            raise PolicyBlocked(f"环境变量 {key} 不在沙箱白名单")
        if not isinstance(value, str):
            raise ValueError(f"环境变量 {key} 的值必须是字符串")


def _safe_execution_arguments(settings: Settings, tool_name: str, arguments: dict) -> dict:
    if tool_name != "execute_sandbox_command":
        return arguments
    safe = dict(arguments)
    env = arguments.get("env") or {}
    if not isinstance(env, dict):
        safe["env"] = {}
        return safe
    sensitive = re.compile(r"(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH|COOKIE)", re.I)
    safe["env"] = {
        str(key).upper(): value
        for key, value in env.items()
        if (
            str(key).upper() in settings.sandbox_allowed_env_key_set
            and not sensitive.search(str(key))
            and isinstance(value, str)
        )
    }
    return safe


def _runtime_from_action(db: Session, settings: Settings, action: ToolAction) -> ToolRuntime:
    run = db.get(AgentRun, action.run_id)
    if run is None:
        raise ValueError("工具 action 关联的 Agent run 不存在")
    context = action.execution_context or {}
    return ToolRuntime(
        db=db,
        settings=settings,
        run=run,
        source_message_id=str(context.get("source_message_id") or ""),
        question=str(context.get("question") or run.question),
        strategy=str(context.get("strategy") or run.retrieval_strategy),
        top_k=context.get("top_k"),
        filters=_filters_from_payload(context.get("filters") or {}),
        citation_catalog=context.get("citation_catalog") or {},
    )


def _call_webhook(settings: Settings, action_id: str, arguments: dict) -> dict:
    url = str(arguments.get("url") or "").strip()
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("Webhook 仅允许 HTTPS URL")
    hostname = parsed.hostname.lower()
    if hostname not in set(settings.tool_webhook_allowed_host_list):
        raise ValueError("Webhook 主机不在服务端白名单")
    for item in socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM):
        address = ipaddress.ip_address(item[4][0])
        if any((address.is_private, address.is_loopback, address.is_link_local, address.is_reserved, address.is_multicast)):
            raise ValueError("Webhook 目标解析到禁止访问的网络地址")

    response = httpx.post(
        url,
        json=arguments.get("payload") or {},
        headers={"Idempotency-Key": action_id, "User-Agent": "agent-loop-tool/1.0"},
        timeout=settings.tool_webhook_timeout_seconds,
        follow_redirects=False,
    )
    response.raise_for_status()
    content = response.content[: max(1, settings.tool_webhook_max_response_bytes)]
    return {
        "called": True,
        "status_code": response.status_code,
        "response_preview": content.decode("utf-8", errors="replace"),
        "truncated": len(response.content) > len(content),
    }


def _filters_payload(filters: RetrievalFilters) -> dict:
    return {
        "tenant_id": filters.tenant_id,
        "workspace_id": filters.workspace_id,
        "document_id": filters.document_id,
        "document_ids": filters.document_ids or [],
        "tags": filters.tags or [],
        "created_from": filters.created_from.isoformat() if filters.created_from else None,
        "created_to": filters.created_to.isoformat() if filters.created_to else None,
        "principal": filters.principal,
        "permission_subjects": filters.permission_subjects or [],
    }


def _filters_from_payload(payload: dict) -> RetrievalFilters:
    def parse_date(value: Any) -> datetime | None:
        if not value:
            return None
        return datetime.fromisoformat(str(value))

    return RetrievalFilters(
        tenant_id=payload.get("tenant_id"), workspace_id=payload.get("workspace_id"),
        document_id=payload.get("document_id"), document_ids=payload.get("document_ids") or [],
        tags=payload.get("tags") or [], created_from=parse_date(payload.get("created_from")),
        created_to=parse_date(payload.get("created_to")), principal=payload.get("principal"),
        permission_subjects=payload.get("permission_subjects") or [],
    )


def _redact(arguments: dict, sensitive_fields: tuple[str, ...]) -> dict:
    return {
        key: ("***" if key in sensitive_fields else value)
        for key, value in arguments.items()
    }


def _argument_summary(arguments: dict, sensitive_fields: tuple[str, ...]) -> str:
    redacted = _redact(arguments, sensitive_fields)
    return _shorten(json.dumps(redacted, ensure_ascii=False, default=str), 500)


def _risk_reason(definition: ToolDefinition) -> str:
    return {
        "low": "只读工具，允许直接执行。",
        "medium": "内部可逆写入；明确用户授权时可直接执行，否则需要审批。",
        "high": "删除、发送或外部调用具有显著副作用，必须人工审批。",
    }[definition.risk_level]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _shorten(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: max(0, limit - 3)] + "..."
