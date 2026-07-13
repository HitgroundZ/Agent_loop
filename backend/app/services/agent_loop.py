from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from time import perf_counter
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import Settings
from app.models import AgentRun, AgentTraceEvent, new_id
from app.services.agent_session_store import AgentSessionStore
from app.services.retrieval import RetrievalConfigurationError, RetrievalFilters, RetrievalService


AgentState = Literal[
    "created",
    "analyzing",
    "retrieving",
    "acting",
    "waiting_approval",
    "evaluating",
    "completed",
    "failed",
    "escalated_to_human",
]
RetrievalStrategy = Literal["vector", "keyword", "hybrid"]

STATE_FLOW: list[str] = [
    "created",
    "analyzing",
    "retrieving",
    "acting",
    "waiting_approval",
    "evaluating",
    "completed",
]
TERMINAL_STATES = {"completed", "failed", "escalated_to_human"}


class AgentLoopService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.retrieval = RetrievalService(settings)
        self.session_store = AgentSessionStore(settings)

    def run(
        self,
        db: Session,
        *,
        question: str,
        session_id: str | None,
        strategy: RetrievalStrategy = "hybrid",
        top_k: int | None = None,
        filters: RetrievalFilters | None = None,
        auto_approve: bool = True,
    ) -> dict:
        normalized_question = _normalize_text(question)
        run = AgentRun(
            id=new_id(),
            session_id=_normalize_text(session_id or "") or new_id(),
            question=normalized_question,
            retrieval_strategy=strategy,
            status="created",
            token_usage=_empty_token_usage(),
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        self.session_store.append_message(run.session_id, "user", normalized_question, run.id)

        sequence = 0
        try:
            start = perf_counter()
            sequence = self._trace(
                db,
                run,
                sequence,
                "created",
                input_payload={
                    "session_id": run.session_id,
                    "question": _shorten(normalized_question, 500),
                    "requested_strategy": strategy,
                    "top_k": top_k,
                    "auto_approve": auto_approve,
                },
                output_summary="已创建智能体运行，并将用户消息写入会话缓存。",
                output_payload={"state_flow": STATE_FLOW, "terminal_states": sorted(TERMINAL_STATES)},
                duration_ms=_duration_ms(start),
            )

            start = perf_counter()
            analysis = self._analyze(normalized_question, strategy, top_k, filters)
            run.plan = analysis["plan"]
            sequence = self._trace(
                db,
                run,
                sequence,
                "analyzing",
                input_payload={"question": normalized_question},
                output_summary=analysis["summary"],
                output_payload=analysis,
                duration_ms=_duration_ms(start),
                plan=analysis["plan"],
            )

            start = perf_counter()
            retrieval_payload, retry_count = self._retrieve(
                db=db,
                query=analysis["rewritten_query"],
                strategy=strategy,
                top_k=top_k,
                filters=filters,
            )
            run.retrieval_result = retrieval_payload
            run.retry_count = max(run.retry_count or 0, retry_count)
            result_count = len(retrieval_payload.get("results") or [])
            retrieval_summary = (
                f"使用{_strategy_label(retrieval_payload.get('strategy', strategy))}"
                f"检索到 {result_count} 条候选引用。"
            )
            if retry_count:
                retrieval_summary += " 已触发降级重试。"
            sequence = self._trace(
                db,
                run,
                sequence,
                "retrieving",
                input_payload={
                    "query": analysis["rewritten_query"],
                    "strategy": strategy,
                    "top_k": top_k,
                    "filters": analysis["filters"],
                },
                output_summary=retrieval_summary,
                output_payload={
                    "strategy": retrieval_payload.get("strategy"),
                    "top_k": retrieval_payload.get("top_k"),
                    "result_count": result_count,
                    "need_human_handoff": retrieval_payload.get("need_human_handoff", False),
                    "fallback_error": retrieval_payload.get("diagnostics", {}).get("fallback_error"),
                },
                duration_ms=_duration_ms(start),
                retry_count=retry_count,
                retrieval=retrieval_payload,
            )

            start = perf_counter()
            answer_payload = self._act(normalized_question, retrieval_payload)
            run.answer = answer_payload["answer"]
            run.citations = answer_payload["citations"]
            sequence = self._trace(
                db,
                run,
                sequence,
                "acting",
                input_payload={
                    "question": normalized_question,
                    "citation_count": len(answer_payload["citations"]),
                },
                output_summary=answer_payload["summary"],
                output_payload={
                    "answer_preview": _shorten(answer_payload["answer"], 500),
                    "citation_count": len(answer_payload["citations"]),
                },
                duration_ms=_duration_ms(start),
            )

            start = perf_counter()
            approval_payload = {
                "approval_required": not auto_approve,
                "approved": bool(auto_approve),
                "reason": "本次问答只读取知识库，不会产生外部副作用。",
            }
            sequence = self._trace(
                db,
                run,
                sequence,
                "waiting_approval",
                input_payload={"auto_approve": auto_approve, "action_type": "answer"},
                output_summary=(
                    "只读问答已自动通过审批。"
                    if auto_approve
                    else "运行正在等待显式审批，审批后再进入评估。"
                ),
                output_payload=approval_payload,
                duration_ms=_duration_ms(start),
            )
            if not auto_approve:
                return self.get_run(db, run.id)

            start = perf_counter()
            evaluation = self._evaluate(answer_payload, retrieval_payload)
            run.evaluation = evaluation
            sequence = self._trace(
                db,
                run,
                sequence,
                "evaluating",
                input_payload={
                    "answer_chars": len(answer_payload["answer"]),
                    "citation_count": len(answer_payload["citations"]),
                },
                output_summary=evaluation["summary"],
                output_payload=evaluation,
                duration_ms=_duration_ms(start),
            )

            terminal_state: AgentState = "completed"
            if evaluation["need_human_handoff"]:
                terminal_state = "escalated_to_human"
            start = perf_counter()
            run.completed_at = datetime.now(timezone.utc)
            sequence = self._trace(
                db,
                run,
                sequence,
                terminal_state,
                input_payload={"evaluation": evaluation},
                output_summary=(
                    "运行完成，已生成有引用支撑的回答。"
                    if terminal_state == "completed"
                    else "未找到可靠引用，运行已提交至人工处理。"
                ),
                output_payload={
                    "answer_preview": _shorten(run.answer or "", 500),
                    "citation_count": len(run.citations or []),
                    "final_state": terminal_state,
                },
                duration_ms=_duration_ms(start),
                terminal=True,
            )
            self.session_store.append_message(run.session_id, "assistant", run.answer or "", run.id)
            return self.get_run(db, run.id)
        except Exception as exc:
            db.rollback()
            run = db.get(AgentRun, run.id) or run
            run.error_message = str(exc)
            run.completed_at = datetime.now(timezone.utc)
            self._trace(
                db,
                run,
                sequence,
                "failed",
                input_payload={"state": run.status, "question": _shorten(normalized_question, 500)},
                output_summary=f"智能体运行失败：{_shorten(str(exc), 500)}",
                output_payload={},
                duration_ms=0,
                error=str(exc),
                terminal=True,
            )
            return self.get_run(db, run.id)

    def get_run(self, db: Session, run_id: str) -> dict:
        run = db.scalar(
            select(AgentRun)
            .options(selectinload(AgentRun.trace_events))
            .where(AgentRun.id == run_id)
        )
        if run is None:
            raise KeyError(run_id)
        return _run_payload(run, self.session_store.get_session(run.session_id))

    def get_session(self, session_id: str) -> dict:
        return self.session_store.get_session(session_id)

    def _analyze(
        self,
        question: str,
        strategy: RetrievalStrategy,
        top_k: int | None,
        filters: RetrievalFilters | None,
    ) -> dict:
        rewritten_query = _normalize_text(question)
        plan = [
            {"step": "analyze", "status": "completed", "summary": "规范化问题并生成检索计划。"},
            {"step": "retrieve", "status": "pending", "summary": f"使用{_strategy_label(strategy)}检索知识库。"},
            {"step": "act", "status": "pending", "summary": "基于检索引用生成回答。"},
            {"step": "evaluate", "status": "pending", "summary": "检查回答是否有足够来源支撑。"},
        ]
        filter_payload = _filters_payload(filters)
        return {
            "summary": f"问题已规范化为 {len(rewritten_query)} 个字符；计划使用{_strategy_label(strategy)}检索。",
            "rewritten_query": rewritten_query,
            "requested_strategy": strategy,
            "top_k": top_k,
            "filters": filter_payload,
            "plan": plan,
        }

    def _retrieve(
        self,
        *,
        db: Session,
        query: str,
        strategy: RetrievalStrategy,
        top_k: int | None,
        filters: RetrievalFilters | None,
    ) -> tuple[dict, int]:
        try:
            return (
                self.retrieval.search(
                    db=db,
                    query=query,
                    strategy=strategy,
                    top_k=top_k,
                    filters=filters,
                ),
                0,
            )
        except RetrievalConfigurationError as exc:
            if strategy == "keyword":
                raise
            fallback = self.retrieval.search(
                db=db,
                query=query,
                strategy="keyword",
                top_k=top_k,
                filters=filters,
            )
            diagnostics = dict(fallback.get("diagnostics") or {})
            diagnostics["fallback_error"] = str(exc)
            diagnostics["requested_strategy"] = strategy
            fallback["diagnostics"] = diagnostics
            return fallback, 1

    def _act(self, question: str, retrieval: dict) -> dict:
        results = retrieval.get("results") or []
        citations = [_citation_payload(index, item) for index, item in enumerate(results[:5], start=1)]
        if not citations:
            return {
                "answer": "没有可靠信息来源，提交至人工处理。",
                "citations": [],
                "summary": "检索未返回候选引用，未生成有来源支撑的回答。",
            }

        answer_lines = [
            "根据知识库中检索到的资料，回答如下：",
            "",
        ]
        for citation in citations[:3]:
            label = citation["label"]
            source = citation["document_name"]
            snippet = _shorten(citation["snippet"], 360)
            answer_lines.append(f"{label}. {source}: {snippet}")
        answer_lines.append("")
        answer_lines.append("引用来源已附在下方，可对照原始切片核验。")
        return {
            "answer": "\n".join(answer_lines),
            "citations": citations,
            "summary": f"已基于 {len(citations)} 条引用生成抽取式回答：{_shorten(question, 120)}",
        }

    def _evaluate(self, answer_payload: dict, retrieval: dict) -> dict:
        citations = answer_payload.get("citations") or []
        need_human_handoff = bool(retrieval.get("need_human_handoff")) or not citations
        confidence = "低" if need_human_handoff else "中"
        return {
            "need_human_handoff": need_human_handoff,
            "confidence": confidence,
            "citation_count": len(citations),
            "summary": (
                "评估未达到来源支撑阈值，需要人工处理。"
                if need_human_handoff
                else "评估通过：回答至少包含一条可检索引用。"
            ),
        }

    def _trace(
        self,
        db: Session,
        run: AgentRun,
        sequence: int,
        state: AgentState,
        *,
        input_payload: dict,
        output_summary: str,
        output_payload: dict,
        duration_ms: int,
        token_usage: dict | None = None,
        error: str | None = None,
        retry_count: int = 0,
        terminal: bool = False,
        plan: list[dict] | None = None,
        retrieval: dict | None = None,
    ) -> int:
        next_sequence = sequence + 1
        tokens = token_usage or _estimate_token_usage(input_payload, output_payload)
        run.status = state
        run.retry_count = max(run.retry_count or 0, retry_count)
        run.token_usage = _merge_token_usage(run.token_usage or {}, tokens)
        event = AgentTraceEvent(
            id=new_id(),
            run_id=run.id,
            session_id=run.session_id,
            sequence=next_sequence,
            state=state,
            input_payload=input_payload,
            output_summary=output_summary,
            output_payload=output_payload,
            duration_ms=max(0, duration_ms),
            token_usage=tokens,
            error=error,
            retry_count=retry_count,
        )
        db.add(run)
        db.add(event)
        db.commit()
        db.refresh(run)
        db.refresh(event)
        self.session_store.save_progress(
            run.session_id,
            run_id=run.id,
            state_name=state,
            trace_event=_trace_payload(event),
            terminal=terminal or state in TERMINAL_STATES,
            plan=plan,
            retrieval=retrieval,
        )
        return next_sequence


def _run_payload(run: AgentRun, session_state: dict) -> dict:
    events = sorted(run.trace_events, key=lambda event: event.sequence)
    return {
        "id": run.id,
        "session_id": run.session_id,
        "question": run.question,
        "status": run.status,
        "current_state": run.status,
        "state_flow": [event.state for event in events],
        "answer": run.answer or "",
        "citations": run.citations or [],
        "plan": run.plan or [],
        "retrieval_result": run.retrieval_result or {},
        "evaluation": run.evaluation or {},
        "token_usage": run.token_usage or {},
        "error_message": run.error_message,
        "retry_count": run.retry_count,
        "trace_events": [_trace_payload(event) for event in events],
        "trace_preview": [
            {
                "sequence": event.sequence,
                "state": event.state,
                "output_summary": event.output_summary,
                "duration_ms": event.duration_ms,
                "token_usage": event.token_usage or {},
                "error": event.error,
                "retry_count": event.retry_count,
            }
            for event in events
        ],
        "session_state": session_state,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


def _trace_payload(event: AgentTraceEvent) -> dict:
    return {
        "id": event.id,
        "run_id": event.run_id,
        "session_id": event.session_id,
        "sequence": event.sequence,
        "state": event.state,
        "input": event.input_payload or {},
        "output_summary": event.output_summary,
        "output": event.output_payload or {},
        "duration_ms": event.duration_ms,
        "token_usage": event.token_usage or {},
        "error": event.error,
        "retry_count": event.retry_count,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def _citation_payload(index: int, item: dict) -> dict:
    return {
        "id": f"C{index}",
        "label": f"[C{index}]",
        "document_id": item.get("document_id"),
        "document_name": item.get("document_name") or "Untitled document",
        "chunk_id": item.get("chunk_id"),
        "chunk_index": item.get("chunk_index"),
        "page": item.get("page"),
        "heading": item.get("heading"),
        "score": item.get("score"),
        "snippet": item.get("snippet") or "",
        "metadata": item.get("metadata") or {},
        "retrieval_source": item.get("retrieval_source"),
    }


def _filters_payload(filters: RetrievalFilters | None) -> dict:
    if filters is None:
        return {}
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


def _strategy_label(strategy: str | None) -> str:
    labels = {
        "hybrid": "混合策略",
        "vector": "向量策略",
        "keyword": "关键词策略",
    }
    return labels.get(strategy or "", strategy or "默认策略")


def _estimate_token_usage(input_payload: Any, output_payload: Any) -> dict:
    input_tokens = _estimate_tokens(input_payload)
    output_tokens = _estimate_tokens(output_payload)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "estimated": True,
    }


def _estimate_tokens(value: Any) -> int:
    if value is None:
        return 0
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, default=str)
    compact = _normalize_text(value)
    if not compact:
        return 0
    cjk_chars = sum(1 for char in compact if "\u4e00" <= char <= "\u9fff")
    words = re.findall(r"[A-Za-z0-9_]+", compact)
    punctuation_budget = max(0, len(compact) - cjk_chars - sum(len(word) for word in words)) // 4
    return max(1, cjk_chars + len(words) + punctuation_budget)


def _merge_token_usage(current: dict, addition: dict) -> dict:
    return {
        "input_tokens": int(current.get("input_tokens", 0)) + int(addition.get("input_tokens", 0)),
        "output_tokens": int(current.get("output_tokens", 0)) + int(addition.get("output_tokens", 0)),
        "total_tokens": int(current.get("total_tokens", 0)) + int(addition.get("total_tokens", 0)),
        "estimated": bool(current.get("estimated", True) or addition.get("estimated", True)),
    }


def _empty_token_usage() -> dict:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "estimated": True,
    }


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _shorten(value: str, limit: int) -> str:
    value = _normalize_text(value)
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)] + "..."


def _duration_ms(start: float) -> int:
    return int((perf_counter() - start) * 1000)
