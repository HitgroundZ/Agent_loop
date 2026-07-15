from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Iterable

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import ConversationMessage, Document, LongTermMemory, new_id
from app.services.agent_session_store import AgentSessionStore

# 长期记忆
MEMORY_CATEGORIES = {
    "event_summary",
    "scene",
    "user_profile",
    "human_correction",
}
ENGLISH_STOP_WORDS = {
    "a", "an", "and", "are", "be", "before", "did", "do", "does", "i", "in",
    "is", "it", "my", "of", "on", "or", "that", "the", "this", "to", "was",
    "what", "when", "where", "who", "why", "you", "your",
}
CJK_STOP_CHARS = set("我你他的了是在有和与请这那什么吗呢啊把被就都也很")


class MemoryService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.short_term = AgentSessionStore(settings)

    def record_message(
        self,
        db: Session,
        *,
        user_id: str,
        session_id: str,
        run_id: str,
        role: str,
        content: str,
    ) -> ConversationMessage:
        message = ConversationMessage(
            id=new_id(),
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            role=role,
            content=content,
        )
        db.add(message)
        db.commit()
        db.refresh(message)
        return message

    def save_tool_memories(
        self,
        db: Session,
        *,
        user_id: str,
        session_id: str,
        run_id: str,
        source_message_id: str,
        candidates: list[dict],
        citation_catalog: dict[str, dict] | None = None,
        require_document_citation: bool = False,
    ) -> list[LongTermMemory]:
        """保存经过工具策略批准的原子长期记忆，不从任意 citation 猜测来源。"""
        source = db.get(ConversationMessage, source_message_id)
        if source is None or source.user_id != user_id or source.run_id != run_id:
            raise ValueError("记忆来源消息不存在或不属于当前运行")

        catalog = citation_catalog or {}
        existing = list(
            db.scalars(
                select(LongTermMemory).where(
                    LongTermMemory.user_id == user_id,
                    LongTermMemory.enabled.is_(True),
                )
            )
        )
        fingerprints = {
            (memory.category, _normalize(memory.content)) for memory in existing
        }
        created: list[LongTermMemory] = []
        for candidate in candidates:
            category = str(candidate.get("category") or "").strip()
            content = re.sub(r"\s+", " ", str(candidate.get("content") or "")).strip()
            if category not in MEMORY_CATEGORIES - {"human_correction"}:
                raise ValueError(f"自动工具不允许创建记忆类型：{category or '-'}")
            if not content:
                raise ValueError("记忆内容不能为空")

            fingerprint = (category, _normalize(content))
            if fingerprint in fingerprints:
                continue

            citation_id = str(candidate.get("citation_id") or "").strip()
            source_document_id: str | None = None
            evidence: dict = {}
            if require_document_citation and not citation_id:
                raise ValueError("文档衍生记忆必须提供一个本轮有效的文档 citation")
            if citation_id:
                citation = catalog.get(citation_id)
                if not citation or citation.get("retrieval_source") == "memory":
                    raise ValueError("文档衍生记忆必须引用本轮唯一有效的文档 citation")
                source_document_id = citation.get("document_id")
                if not source_document_id:
                    raise ValueError("citation 未包含可追溯的 document_id")
                evidence = {"citation_id": citation_id, "chunk_id": citation.get("chunk_id")}

            memory = LongTermMemory(
                id=new_id(),
                user_id=user_id,
                category=category,
                content=content,
                source_message_id=source_message_id,
                source_document_id=source_document_id,
                metadata_json={
                    "session_id": session_id,
                    "run_id": run_id,
                    "generated": True,
                    "kind": "tool_selected_memory",
                    "reason": str(candidate.get("reason") or "").strip(),
                    **evidence,
                },
            )
            db.add(memory)
            created.append(memory)
            fingerprints.add(fingerprint)

        if created:
            db.commit()
            for memory in created:
                db.refresh(memory)
            self.short_term.invalidate_memory_cache(user_id)
        return created
    # 检索长期记忆
    def search(
        self,
        db: Session,
        *,
        user_id: str,
        session_id: str,
        query: str,
    ) -> dict:
        normalized_query = _normalize(query)
        cached = self.short_term.get_memory_cache(user_id, session_id, normalized_query)
        if cached is not None:
            return {**cached, "cached": True}

        candidates = list(
            db.scalars(
                select(LongTermMemory)
                .where(
                    LongTermMemory.user_id == user_id,
                    LongTermMemory.enabled.is_(True),
                )
                .order_by(desc(LongTermMemory.created_at))
                .limit(max(1, self.settings.memory_candidate_limit))
            )
        )
        query_terms = _search_terms(normalized_query)
        scored: list[tuple[float, LongTermMemory]] = []
        for memory in candidates:
            score = _relevance_score(normalized_query, query_terms, memory)
            if score > 0:
                scored.append((score, memory))
        scored.sort(key=lambda item: (item[0], item[1].created_at), reverse=True)

        selected: list[tuple[float, LongTermMemory]] = []
        used_chars = 0
        seen_content: set[str] = set()
        for score, memory in scored:
            fingerprint = _normalize(memory.content)                        # 内容去重
            if fingerprint in seen_content:
                continue
            if selected and used_chars + len(memory.content) > self.settings.memory_context_max_chars:
                continue
            selected.append((score, memory))
            seen_content.add(fingerprint)
            used_chars += len(memory.content)
            if len(selected) >= max(1, self.settings.memory_retrieval_limit):
                break

        now = datetime.now(timezone.utc)
        for _, memory in selected:
            memory.access_count = int(memory.access_count or 0) + 1
            memory.last_accessed_at = now
            db.add(memory)
        if selected:
            db.commit()

        source_messages = self._source_message_map(
            db, [memory.source_message_id for _, memory in selected]
        )
        items = [
            _memory_payload(memory, source_messages.get(memory.source_message_id), score=score)
            for score, memory in selected
        ]
        payload = {
            "query": normalized_query,
            "user_id": user_id,
            "items": items,
            "candidate_count": len(candidates),
            "injected_count": len(items),
            "injected_chars": sum(len(item["content"]) for item in items),
            "cached": False,
        }
        self.short_term.set_memory_cache(user_id, session_id, normalized_query, payload)
        return payload
    # 用于查看长期记忆
    def list_memories(
        self,
        db: Session,
        *,
        user_id: str,
        category: str | None = None,
        enabled: bool | None = None,
        limit: int = 100,
    ) -> dict:
        statement = select(LongTermMemory).where(LongTermMemory.user_id == user_id)
        if category:
            statement = statement.where(LongTermMemory.category == category)
        if enabled is not None:
            statement = statement.where(LongTermMemory.enabled.is_(enabled))
        memories = list(
            db.scalars(statement.order_by(desc(LongTermMemory.created_at)).limit(min(500, max(1, limit))))
        )
        source_messages = self._source_message_map(
            db, [memory.source_message_id for memory in memories]
        )
        return {
            "user_id": user_id,
            "items": [
                _memory_payload(memory, source_messages.get(memory.source_message_id))
                for memory in memories
            ],
            "count": len(memories),
        }

    def list_messages(self, db: Session, *, user_id: str, limit: int = 100) -> dict:
        messages = list(
            db.scalars(
                select(ConversationMessage)
                .where(ConversationMessage.user_id == user_id)
                .order_by(desc(ConversationMessage.created_at))
                .limit(min(500, max(1, limit)))
            )
        )
        return {
            "user_id": user_id,
            "items": [_message_payload(message) for message in messages],
            "count": len(messages),
        }
    # 人工创建记忆
    def create_memory(
        self,
        db: Session,
        *,
        user_id: str,
        category: str,
        content: str,
        source_message_id: str | None,
        source_document_id: str | None,
        metadata: dict | None = None,
    ) -> dict:
        if category not in MEMORY_CATEGORIES:
            raise ValueError("不支持的记忆类型")
        if category == "human_correction":
            raise ValueError("human_correction 只能通过人工纠错接口创建")
        if not source_message_id and not source_document_id:
            raise ValueError("长期记忆必须包含 source_message_id 或 source_document_id")
        if source_message_id:
            source = db.get(ConversationMessage, source_message_id)
            if source is None or source.user_id != user_id:
                raise ValueError("来源消息不存在或不属于该用户")
        if source_document_id and db.get(Document, source_document_id) is None:
            raise ValueError("来源文档不存在")
        normalized_content = _normalize(content)
        existing = list(db.scalars(
            select(LongTermMemory).where(
                LongTermMemory.user_id == user_id,
                LongTermMemory.category == category,
                LongTermMemory.enabled.is_(True),
            )
        ))
        duplicate = next(
            (memory for memory in existing if _normalize(memory.content) == normalized_content),
            None,
        )
        if duplicate is not None:
            source = db.get(ConversationMessage, duplicate.source_message_id) if duplicate.source_message_id else None
            return _memory_payload(duplicate, source)
        memory = LongTermMemory(
            id=new_id(),
            user_id=user_id,
            category=category,
            content=content.strip(),
            source_message_id=source_message_id,
            source_document_id=source_document_id,
            metadata_json=metadata or {},
        )
        db.add(memory)
        db.commit()
        db.refresh(memory)
        self.short_term.invalidate_memory_cache(user_id)
        source = db.get(ConversationMessage, source_message_id) if source_message_id else None
        return _memory_payload(memory, source)

    def set_enabled(self, db: Session, memory_id: str, enabled: bool) -> dict:
        memory = self._get_memory(db, memory_id)
        memory.enabled = enabled
        db.add(memory)
        db.commit()
        db.refresh(memory)
        self.short_term.invalidate_memory_cache(memory.user_id)
        source = db.get(ConversationMessage, memory.source_message_id) if memory.source_message_id else None
        return _memory_payload(memory, source)

    def correct(
        self,
        db: Session,
        memory_id: str,
        *,
        corrected_content: str,
        reason: str | None,
    ) -> dict:
        original = self._get_memory(db, memory_id)
        original.enabled = False
        correction = LongTermMemory(
            id=new_id(),
            user_id=original.user_id,
            category="human_correction",
            content=corrected_content.strip(),
            source_message_id=original.source_message_id,
            source_document_id=original.source_document_id,
            parent_memory_id=original.id,
            metadata_json={
                "reason": (reason or "").strip(),
                "provenance": "explicit_human_correction",
                "original_category": original.category,
                "original_content": original.content,
                "original_source_message_id": original.source_message_id,
                "original_source_document_id": original.source_document_id,
            },
        )
        db.add_all([original, correction])
        db.commit()
        db.refresh(correction)
        self.short_term.invalidate_memory_cache(original.user_id)
        source = db.get(ConversationMessage, correction.source_message_id) if correction.source_message_id else None
        return _memory_payload(correction, source)

    def delete(self, db: Session, memory_id: str) -> dict:
        memory = self._get_memory(db, memory_id)
        user_id = memory.user_id
        db.delete(memory)
        db.commit()
        self.short_term.invalidate_memory_cache(user_id)
        return {"deleted": True, "id": memory_id, "user_id": user_id}

    @staticmethod
    def _get_memory(db: Session, memory_id: str) -> LongTermMemory:
        memory = db.get(LongTermMemory, memory_id)
        if memory is None:
            raise KeyError(memory_id)
        return memory

    @staticmethod
    def _source_message_map(
        db: Session, source_ids: Iterable[str | None]
    ) -> dict[str, ConversationMessage]:
        ids = {source_id for source_id in source_ids if source_id}
        if not ids:
            return {}
        return {
            message.id: message
            for message in db.scalars(
                select(ConversationMessage).where(ConversationMessage.id.in_(ids))
            )
        }

# 通过计算共同词的数量来判断是否相关的
def _relevance_score(
    normalized_query: str,
    query_terms: set[str],
    memory: LongTermMemory,
) -> float:
    content = _normalize(memory.content)
    if not content or not query_terms:
        return 0.0
    memory_terms = _search_terms(content)
    overlap = query_terms & memory_terms
    if not overlap:
        return 0.0
    score = len(overlap) / max(1, len(query_terms))
    if normalized_query in content or content in normalized_query:
        score += 0.7
    score += {
        "human_correction": 0.25,
        "user_profile": 0.18,
        "scene": 0.08,
        "event_summary": 0.04,
    }.get(memory.category, 0.0)
    return round(score, 6)


def _search_terms(value: str) -> set[str]:
    lowered = value.lower()
    terms = {
        term
        for term in re.findall(r"[a-z0-9_]+", lowered)
        if term not in ENGLISH_STOP_WORDS
    }
    cjk_sequences = re.findall(r"[\u4e00-\u9fff]+", lowered)
    for sequence in cjk_sequences:
        terms.update(char for char in sequence if char not in CJK_STOP_CHARS)
        terms.update(sequence[index : index + 2] for index in range(max(0, len(sequence) - 1)))
    return {term for term in terms if term}


def _looks_like_profile_fact(value: str) -> bool:
    normalized = _normalize(value)
    explicit_memory_markers = ("请记住", "记住我", "remember that i")
    question_markers = ("什么", "哪个", "哪种", "是否", "吗", "么", "?", "？")
    if any(marker in normalized for marker in question_markers) and not any(
        marker in normalized for marker in explicit_memory_markers
    ):
        return False
    markers = (
        "我叫",
        "我的名字",
        "请记住",
        "记住我",
        "我喜欢",
        "我偏好",
        "我习惯",
        "我不喜欢",
        "我的职业",
        "我是一个",
        "my name",
        "remember that i",
        "i like",
        "i prefer",
        "my favorite",
        "i dislike",
        "i am a",
    )
    return any(marker in normalized for marker in markers)


def _memory_payload(
    memory: LongTermMemory,
    source_message: ConversationMessage | None,
    *,
    score: float | None = None,
) -> dict:
    return {
        "id": memory.id,
        "user_id": memory.user_id,
        "category": memory.category,
        "content": memory.content,
        "enabled": memory.enabled,
        "source_message_id": memory.source_message_id,
        "source_document_id": memory.source_document_id,
        "parent_memory_id": memory.parent_memory_id,
        "metadata": memory.metadata_json or {},
        "access_count": memory.access_count or 0,
        "last_accessed_at": memory.last_accessed_at.isoformat() if memory.last_accessed_at else None,
        "created_at": memory.created_at.isoformat() if memory.created_at else None,
        "updated_at": memory.updated_at.isoformat() if memory.updated_at else None,
        "score": score,
        "source_message": _message_payload(source_message) if source_message else None,
    }


def _message_payload(message: ConversationMessage) -> dict:
    return {
        "id": message.id,
        "user_id": message.user_id,
        "session_id": message.session_id,
        "run_id": message.run_id,
        "role": message.role,
        "content": message.content,
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().lower()


def _shorten(value: str, limit: int) -> str:
    normalized = re.sub(r"\s+", " ", value or "").strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 3)] + "..."
