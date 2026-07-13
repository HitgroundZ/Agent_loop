from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
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

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        run_id: str,
        *,
        user_id: str = "default",
        message_id: str | None = None,
    ) -> dict:
        return self._mutate(
            session_id,
            lambda state: self._append_message(
                state, role, content, run_id, user_id=user_id, message_id=message_id
            ),
        )

    def check_request_limits(self, user_id: str, session_id: str) -> dict:
        """检查固定窗口限流和每日 token budget；Redis 故障时 fail-open。"""
        now = datetime.now(timezone.utc)
        window = max(1, self.settings.agent_rate_limit_window_seconds)
        window_id = int(now.timestamp()) // window
        rate_key = f"agent_loop:rate_limit:{user_id}:{window_id}"
        budget_key = self._budget_key(user_id, now)
        try:
            pipe = self.client.pipeline()
            pipe.incr(rate_key)
            pipe.expire(rate_key, window + 5)
            pipe.get(budget_key)
            result = pipe.execute()
            request_count = int(result[0] or 0)
            used_tokens = int(result[2] or 0)
            rate_limit = max(1, self.settings.agent_rate_limit_requests)
            token_budget = max(1, self.settings.agent_token_budget)
            snapshot = {
                "allowed": request_count <= rate_limit and used_tokens < token_budget,
                "rate_limit": {
                    "requests": request_count,
                    "limit": rate_limit,
                    "window_seconds": window,
                    "remaining": max(0, rate_limit - request_count),
                },
                "token_budget": {
                    "used": used_tokens,
                    "limit": token_budget,
                    "remaining": max(0, token_budget - used_tokens),
                    "period": now.date().isoformat(),
                },
                "available": True,
                "error": None,
            }
            if request_count > rate_limit:
                snapshot["reason"] = "rate_limit_exceeded"
            elif used_tokens >= token_budget:
                snapshot["reason"] = "token_budget_exceeded"
            self._mutate(
                session_id,
                lambda state: self._set_limit_snapshot(state, user_id, snapshot),
            )
            self.last_error = None
            return snapshot
        except RedisError as exc:
            self.last_error = str(exc)
            return {
                "allowed": True,
                "reason": "redis_unavailable_fail_open",
                "rate_limit": {},
                "token_budget": {},
                "available": False,
                "error": self.last_error,
            }

    def consume_token_budget(self, user_id: str, session_id: str, tokens: int) -> dict:
        amount = max(0, int(tokens or 0))
        now = datetime.now(timezone.utc)
        budget_key = self._budget_key(user_id, now)
        try:
            pipe = self.client.pipeline()
            pipe.incrby(budget_key, amount)
            pipe.expire(budget_key, 2 * 24 * 60 * 60)
            result = pipe.execute()
            used = int(result[0] or 0)
            limit = max(1, self.settings.agent_token_budget)
            snapshot = {
                "used": used,
                "limit": limit,
                "remaining": max(0, limit - used),
                "period": now.date().isoformat(),
            }
            self._mutate(
                session_id,
                lambda state: self._set_token_budget(state, user_id, snapshot),
            )
            self.last_error = None
            return {"saved": True, "available": True, **snapshot}
        except RedisError as exc:
            self.last_error = str(exc)
            return {"saved": False, "available": False, "error": self.last_error}

    def set_pending_approval(self, session_id: str, run_id: str, user_id: str) -> dict:
        return self._mutate(
            session_id,
            lambda state: self._set_pending_approval(state, run_id, user_id),
        )

    def clear_pending_approval(self, session_id: str, run_id: str) -> dict:
        def mutate(state: dict) -> dict:
            pending = state.get("pending_approval") or {}
            if not pending or pending.get("run_id") == run_id:
                state["pending_approval"] = {}
            state["updated_at"] = _now_iso()
            return state

        return self._mutate(session_id, mutate)

    def get_memory_cache(self, user_id: str, session_id: str, query: str) -> dict | None:
        try:
            version = self.client.get(self._memory_version_key(user_id)) or "0"
            key = self._memory_cache_key(user_id, version, query)
            raw = self.client.get(key)
            self.last_error = None
            if not raw:
                self._save_cache_snapshot(session_id, query, hit=False, result_count=0)
                return None
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                return None
            self._save_cache_snapshot(
                session_id,
                query,
                hit=True,
                result_count=len(payload.get("items") or []),
            )
            return payload
        except (RedisError, json.JSONDecodeError) as exc:
            self.last_error = str(exc)
            return None

    def set_memory_cache(self, user_id: str, session_id: str, query: str, payload: dict) -> None:
        try:
            version = self.client.get(self._memory_version_key(user_id)) or "0"
            key = self._memory_cache_key(user_id, version, query)
            self.client.set(
                key,
                json.dumps(payload, ensure_ascii=False, default=str),
                ex=max(1, self.settings.memory_cache_ttl_seconds),
            )
            self._save_cache_snapshot(
                session_id,
                query,
                hit=False,
                result_count=len(payload.get("items") or []),
            )
            self.last_error = None
        except RedisError as exc:
            self.last_error = str(exc)

    def invalidate_memory_cache(self, user_id: str) -> None:
        try:
            self.client.incr(self._memory_version_key(user_id))
            self.last_error = None
        except RedisError as exc:
            self.last_error = str(exc)

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

    def _append_message(
        self,
        state: dict,
        role: str,
        content: str,
        run_id: str,
        *,
        user_id: str,
        message_id: str | None,
    ) -> dict:
        state["user_id"] = user_id
        messages = state.setdefault("recent_messages", [])
        messages.append(
            {
                "id": message_id,
                "role": role,
                "content": content,
                "run_id": run_id,
                "created_at": _now_iso(),
            }
        )
        state["recent_messages"] = messages[-self.message_limit :]
        state["updated_at"] = _now_iso()
        return state

    @staticmethod
    def _set_limit_snapshot(state: dict, user_id: str, snapshot: dict) -> dict:
        state["user_id"] = user_id
        state["rate_limit"] = snapshot.get("rate_limit") or {}
        state["token_budget"] = snapshot.get("token_budget") or {}
        state["updated_at"] = _now_iso()
        return state

    @staticmethod
    def _set_token_budget(state: dict, user_id: str, snapshot: dict) -> dict:
        state["user_id"] = user_id
        state["token_budget"] = snapshot
        state["updated_at"] = _now_iso()
        return state

    @staticmethod
    def _set_pending_approval(state: dict, run_id: str, user_id: str) -> dict:
        state["user_id"] = user_id
        state["pending_approval"] = {
            "run_id": run_id,
            "status": "pending",
            "created_at": _now_iso(),
        }
        state["updated_at"] = _now_iso()
        return state

    def _save_cache_snapshot(
        self, session_id: str, query: str, *, hit: bool, result_count: int
    ) -> None:
        self._mutate(
            session_id,
            lambda state: {
                **state,
                "retrieval_cache": {
                    "query_hash": hashlib.sha256(query.encode("utf-8")).hexdigest()[:16],
                    "hit": hit,
                    "result_count": result_count,
                    "updated_at": _now_iso(),
                },
                "updated_at": _now_iso(),
            },
        )

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

    @staticmethod
    def _budget_key(user_id: str, now: datetime) -> str:
        return f"agent_loop:token_budget:{user_id}:{now.date().isoformat()}"

    @staticmethod
    def _memory_version_key(user_id: str) -> str:
        return f"agent_loop:memory_version:{user_id}"

    @staticmethod
    def _memory_cache_key(user_id: str, version: str, query: str) -> str:
        query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()
        return f"agent_loop:memory_cache:{user_id}:{version}:{query_hash}"


def _empty_session(session_id: str) -> dict:
    return {
        "session_id": session_id,
        "user_id": None,
        "recent_messages": [],
        "pending_approval": {},
        "retrieval_cache": {},
        "rate_limit": {},
        "token_budget": {},
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
