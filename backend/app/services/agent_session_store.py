from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from typing import Any, Callable

from redis import Redis
from redis.exceptions import RedisError

from app.config import Settings


class AgentSessionStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.message_limit = max(1, settings.agent_session_message_limit)
        self.ttl_seconds = max(60, settings.agent_session_ttl_seconds)
        self.last_error: str | None = None
        self._client: Redis | None = None

    def append_message(self, session_id: str, role: str, content: str, run_id: str) -> dict:
        return self._mutate(
            session_id,
            lambda state: self._append_message(state, role, content, run_id),
        )

    def save_progress(
        self,
        session_id: str,
        *,
        run_id: str,
        state_name: str,
        trace_event: dict,
        terminal: bool = False,
        plan: list[dict] | None = None,
        retrieval: dict | None = None,
    ) -> dict:
        def mutate(state: dict) -> dict:
            now = _now_iso()
            state["task_status"] = {
                "run_id": run_id,
                "state": state_name,
                "terminal": terminal,
                "updated_at": now,
                "retry_count": trace_event.get("retry_count", 0),
                "error": trace_event.get("error"),
            }
            if plan is not None:
                state["temporary_plan"] = plan
            if retrieval is not None:
                state["retrieval_intermediate"] = _retrieval_snapshot(run_id, retrieval)
            trace_preview = state.setdefault("trace_preview", [])
            trace_preview.append(_trace_preview(trace_event))
            state["trace_preview"] = trace_preview[-20:]
            state["updated_at"] = now
            return state

        return self._mutate(session_id, mutate)

    def get_session(self, session_id: str) -> dict:
        state = self._read(session_id)
        state["redis"] = {
            "available": self.last_error is None,
            "error": self.last_error,
        }
        return state

    def _append_message(self, state: dict, role: str, content: str, run_id: str) -> dict:
        messages = state.setdefault("recent_messages", [])
        messages.append(
            {
                "role": role,
                "content": content,
                "run_id": run_id,
                "created_at": _now_iso(),
            }
        )
        state["recent_messages"] = messages[-self.message_limit :]
        state["updated_at"] = _now_iso()
        return state

    def _mutate(self, session_id: str, mutator: Callable[[dict], dict]) -> dict:
        try:
            state = mutator(self._read(session_id))
            self._write(session_id, state)
            self.last_error = None
            return {
                "saved": True,
                "available": True,
                "error": None,
            }
        except RedisError as exc:
            self.last_error = str(exc)
            return {
                "saved": False,
                "available": False,
                "error": self.last_error,
            }

    def _read(self, session_id: str) -> dict:
        try:
            raw = self.client.get(self._key(session_id))
            self.last_error = None
        except RedisError as exc:
            self.last_error = str(exc)
            return _empty_session(session_id)
        if not raw:
            return _empty_session(session_id)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return _empty_session(session_id)
        if not isinstance(data, dict):
            return _empty_session(session_id)
        return {
            **_empty_session(session_id),
            **data,
            "session_id": session_id,
        }

    def _write(self, session_id: str, state: dict) -> None:
        clean = deepcopy(state)
        clean.pop("redis", None)
        self.client.set(self._key(session_id), json.dumps(clean, ensure_ascii=False, default=str), ex=self.ttl_seconds)

    @property
    def client(self) -> Redis:
        if self._client is None:
            self._client = Redis.from_url(
                self.settings.redis_url,
                decode_responses=True,
                socket_timeout=2,
                socket_connect_timeout=2,
            )
        return self._client

    @staticmethod
    def _key(session_id: str) -> str:
        return f"agent_loop:sessions:{session_id}"


def _empty_session(session_id: str) -> dict:
    return {
        "session_id": session_id,
        "recent_messages": [],
        "task_status": {},
        "temporary_plan": [],
        "retrieval_intermediate": {},
        "trace_preview": [],
        "updated_at": None,
    }


def _trace_preview(event: dict) -> dict:
    return {
        "sequence": event.get("sequence"),
        "state": event.get("state"),
        "output_summary": event.get("output_summary"),
        "duration_ms": event.get("duration_ms"),
        "token_usage": event.get("token_usage") or {},
        "error": event.get("error"),
        "retry_count": event.get("retry_count", 0),
    }


def _retrieval_snapshot(run_id: str, retrieval: dict) -> dict:
    results = retrieval.get("results") or []
    return {
        "run_id": run_id,
        "query": retrieval.get("query"),
        "rewritten_query": retrieval.get("rewritten_query"),
        "strategy": retrieval.get("strategy"),
        "top_k": retrieval.get("top_k"),
        "need_human_handoff": retrieval.get("need_human_handoff", False),
        "result_count": len(results),
        "results": [
            {
                "document_id": item.get("document_id"),
                "document_name": item.get("document_name"),
                "chunk_id": item.get("chunk_id"),
                "chunk_index": item.get("chunk_index"),
                "score": item.get("score"),
                "snippet": item.get("snippet"),
            }
            for item in results[:5]
        ],
        "diagnostics": retrieval.get("diagnostics") or {},
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
