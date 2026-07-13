"""生成一组可长期保留、便于人工查看的 Day5 示例数据。"""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import engine
from app.models import AgentRun, Document, LongTermMemory, new_id
from app.services.memory import MemoryService


DEMO_USER_ID = "day5-structure-demo"
SESSION_A = "day5-demo-session-a"
SESSION_B = "day5-demo-session-b"


def main() -> None:
    service = MemoryService(get_settings())
    with Session(engine, expire_on_commit=False) as db:
        # 只重建固定 demo 用户，不影响任何真实用户数据。
        db.execute(delete(LongTermMemory).where(LongTermMemory.user_id == DEMO_USER_ID))
        db.execute(delete(AgentRun).where(AgentRun.user_id == DEMO_USER_ID))
        db.commit()
        service.short_term.invalidate_memory_cache(DEMO_USER_ID)

        source_document = db.scalar(select(Document).order_by(Document.created_at).limit(1))
        run_a = AgentRun(
            id=new_id(),
            user_id=DEMO_USER_ID,
            session_id=SESSION_A,
            question="请记住我喜欢手冲咖啡，并偏好简洁回答。",
            status="completed",
            retrieval_strategy="keyword",
            answer="已记录用户偏好。",
        )
        db.add(run_a)
        db.commit()
        user_message = service.record_message(
            db,
            user_id=DEMO_USER_ID,
            session_id=SESSION_A,
            run_id=run_a.id,
            role="user",
            content=run_a.question,
        )
        service.record_message(
            db,
            user_id=DEMO_USER_ID,
            session_id=SESSION_A,
            run_id=run_a.id,
            role="assistant",
            content=run_a.answer or "",
        )
        generated = service.persist_run_memories(
            db,
            user_id=DEMO_USER_ID,
            session_id=SESSION_A,
            run_id=run_a.id,
            question=run_a.question,
            answer=run_a.answer or "",
            source_message_id=user_message.id,
            citations=(
                [{"document_id": source_document.id}]
                if source_document is not None
                else []
            ),
        )
        profile = next(memory for memory in generated if memory.category == "user_profile")
        correction = service.correct(
            db,
            profile.id,
            corrected_content="用户喜欢手冲咖啡，并偏好简洁回答（人工核验）。",
            reason="人工核验原始偏好，修正自动摘要的表达方式，不改变用户事实",
        )

        recalled = service.search(
            db,
            user_id=DEMO_USER_ID,
            session_id=SESSION_B,
            query="我喜欢什么饮品？",
        )
        if not recalled["items"] or "手冲咖啡" not in recalled["items"][0]["content"]:
            raise RuntimeError("Demo 召回结果与用户原始偏好不一致")
        if any("茉莉花茶" in item["content"] for item in recalled["items"]):
            raise RuntimeError("Demo 不允许生成无来源的茉莉花茶偏好")
        recalled_preference = recalled["items"][0]["content"]
        run_b = AgentRun(
            id=new_id(),
            user_id=DEMO_USER_ID,
            session_id=SESSION_B,
            question="我喜欢什么饮品？",
            status="completed",
            retrieval_strategy="keyword",
            answer=f"根据已核验的长期记忆：{recalled_preference}",
            memory_context=recalled["items"],
            citations=[
                {
                    "id": "M1",
                    "memory_id": correction["id"],
                    "source_message_id": correction["source_message_id"],
                    "retrieval_source": "memory",
                }
            ],
        )
        db.add(run_b)
        db.commit()
        message_b = service.record_message(
            db,
            user_id=DEMO_USER_ID,
            session_id=SESSION_B,
            run_id=run_b.id,
            role="user",
            content=run_b.question,
        )
        service.record_message(
            db,
            user_id=DEMO_USER_ID,
            session_id=SESSION_B,
            run_id=run_b.id,
            role="assistant",
            content=run_b.answer or "",
        )
        service.persist_run_memories(
            db,
            user_id=DEMO_USER_ID,
            session_id=SESSION_B,
            run_id=run_b.id,
            question=run_b.question,
            answer=run_b.answer or "",
            source_message_id=message_b.id,
            citations=run_b.citations,
        )

        memory_count = len(
            list(db.scalars(select(LongTermMemory).where(LongTermMemory.user_id == DEMO_USER_ID)))
        )
        print("Day 5 demo data created")
        print(f"user_id={DEMO_USER_ID}")
        print(f"run_a={run_a.id}, run_b={run_b.id}")
        print(f"source_message_id={user_message.id}")
        print(f"human_correction_id={correction['id']}")
        print(f"memory_count={memory_count}")


if __name__ == "__main__":
    main()
