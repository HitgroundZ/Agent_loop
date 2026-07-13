from __future__ import annotations

import os
import unittest
from uuid import uuid4

from redis.exceptions import RedisError
from sqlalchemy import create_engine, delete, inspect, select, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AgentRun, ConversationMessage, LongTermMemory, new_id
from app.services.memory import MemoryService, _looks_like_profile_fact


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://agent_loop:agent_loop@127.0.0.1:5432/agent_loop",
)


class Day5MemoryIntegrationTest(unittest.TestCase):
    """验证 Day5 表结构，以及一条记忆从生成到纠错、禁用、删除的完整生命周期。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = create_engine(DATABASE_URL, pool_pre_ping=True)
        cls.inspector = inspect(cls.engine)
        cls.settings = Settings()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()

    def setUp(self) -> None:
        suffix = uuid4().hex[:10]
        self.user_id = f"day5-unittest-{suffix}"
        self.session_ids = [f"{self.user_id}-session-a", f"{self.user_id}-session-b"]

    def tearDown(self) -> None:
        # 服务方法会主动 commit，因此使用独立清理事务，并只清理本测试的唯一用户。
        with Session(self.engine) as db:
            db.execute(delete(LongTermMemory).where(LongTermMemory.user_id == self.user_id))
            db.execute(delete(AgentRun).where(AgentRun.user_id == self.user_id))
            db.commit()
        self._clear_test_redis_keys()

    def test_schema_contract(self) -> None:
        table_names = set(self.inspector.get_table_names())
        self.assertTrue(
            {"agent_runs", "conversation_messages", "long_term_memories"}.issubset(table_names)
        )

        memory_columns = {
            column["name"]: column for column in self.inspector.get_columns("long_term_memories")
        }
        expected_memory_columns = {
            "id",
            "user_id",
            "category",
            "content",
            "source_message_id",
            "source_document_id",
            "parent_memory_id",
            "enabled",
            "metadata",
            "access_count",
            "last_accessed_at",
            "created_at",
            "updated_at",
        }
        self.assertEqual(expected_memory_columns, set(memory_columns))
        for required_column in ("id", "user_id", "category", "content", "enabled", "metadata"):
            self.assertFalse(memory_columns[required_column]["nullable"], required_column)
        self.assertTrue(memory_columns["source_message_id"]["nullable"])
        self.assertTrue(memory_columns["source_document_id"]["nullable"])

        memory_indexes = {
            index["name"] for index in self.inspector.get_indexes("long_term_memories")
        }
        self.assertTrue(
            {
                "ix_long_term_memories_user_id",
                "ix_long_term_memories_category",
                "ix_long_term_memories_source_message_id",
                "ix_long_term_memories_source_document_id",
                "ix_long_term_memories_enabled",
                "ix_long_term_memories_user_enabled",
            }.issubset(memory_indexes)
        )

        message_columns = {
            column["name"] for column in self.inspector.get_columns("conversation_messages")
        }
        self.assertEqual(
            {"id", "user_id", "session_id", "run_id", "role", "content", "created_at"},
            message_columns,
        )
        message_foreign_keys = self.inspector.get_foreign_keys("conversation_messages")
        self.assertEqual(1, len(message_foreign_keys))
        self.assertEqual("agent_runs", message_foreign_keys[0]["referred_table"])
        self.assertEqual(["run_id"], message_foreign_keys[0]["constrained_columns"])

        run_columns = {column["name"] for column in self.inspector.get_columns("agent_runs")}
        self.assertTrue({"user_id", "memory_context"}.issubset(run_columns))

        # 来源 ID 是稳定追溯标识，不设级联外键，避免源文档删除时被自动置空。
        self.assertEqual([], self.inspector.get_foreign_keys("long_term_memories"))

        with Session(self.engine) as db:
            revision = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        self.assertEqual("202607060001", revision)

    def test_profile_fact_does_not_treat_a_question_as_a_fact(self) -> None:
        self.assertTrue(_looks_like_profile_fact("请记住我喜欢手冲咖啡。"))
        self.assertTrue(_looks_like_profile_fact("I prefer concise answers."))
        self.assertFalse(_looks_like_profile_fact("我喜欢什么饮品？"))
        self.assertFalse(_looks_like_profile_fact("Do I prefer concise answers?"))

    def test_memory_lifecycle_and_cross_session_recall(self) -> None:
        service = MemoryService(self.settings)
        question = "请记住我喜欢手冲咖啡，并且偏好简洁回答。"

        with Session(self.engine, expire_on_commit=False) as db:
            run = AgentRun(
                id=new_id(),
                user_id=self.user_id,
                session_id=self.session_ids[0],
                question=question,
                status="completed",
                retrieval_strategy="keyword",
                answer="已记录用户偏好。",
            )
            db.add(run)
            db.commit()

            source_message = service.record_message(
                db,
                user_id=self.user_id,
                session_id=self.session_ids[0],
                run_id=run.id,
                role="user",
                content=question,
            )
            generated = service.persist_run_memories(
                db,
                user_id=self.user_id,
                session_id=self.session_ids[0],
                run_id=run.id,
                question=question,
                answer=run.answer or "",
                source_message_id=source_message.id,
                citations=[],
            )

            self.assertEqual(
                {"event_summary", "scene", "user_profile"},
                {memory.category for memory in generated},
            )
            self.assertTrue(
                all(memory.source_message_id == source_message.id for memory in generated)
            )

            recalled = service.search(
                db,
                user_id=self.user_id,
                session_id=self.session_ids[1],
                query="我之前说喜欢什么饮品？",
            )
            self.assertGreaterEqual(recalled["injected_count"], 1)
            self.assertTrue(all("手冲咖啡" in item["content"] for item in recalled["items"]))
            self.assertTrue(all("茉莉花茶" not in item["content"] for item in recalled["items"]))
            self.assertTrue(
                all(item["source_message_id"] == source_message.id for item in recalled["items"])
            )
            self.assertEqual(question, recalled["items"][0]["source_message"]["content"])

            profile = next(memory for memory in generated if memory.category == "user_profile")
            correction = service.correct(
                db,
                profile.id,
                corrected_content="用户喜欢茉莉花茶，不喜欢咖啡。",
                reason="用户人工纠正偏好",
            )
            db.expire_all()
            corrected_original = db.get(LongTermMemory, profile.id)
            self.assertIsNotNone(corrected_original)
            self.assertFalse(corrected_original.enabled)
            self.assertEqual("human_correction", correction["category"])
            self.assertEqual(profile.id, correction["parent_memory_id"])
            self.assertEqual(source_message.id, correction["source_message_id"])
            self.assertEqual(
                "explicit_human_correction", correction["metadata"]["provenance"]
            )
            self.assertEqual(
                source_message.id,
                correction["metadata"]["original_source_message_id"],
            )

            recalled_after_correction = service.search(
                db,
                user_id=self.user_id,
                session_id=self.session_ids[1],
                query="我喜欢什么饮品？",
            )
            self.assertEqual(correction["id"], recalled_after_correction["items"][0]["id"])
            self.assertIn("茉莉花茶", recalled_after_correction["items"][0]["content"])

            enabled_memories = list(
                db.scalars(
                    select(LongTermMemory).where(
                        LongTermMemory.user_id == self.user_id,
                        LongTermMemory.enabled.is_(True),
                    )
                )
            )
            for memory in enabled_memories:
                service.set_enabled(db, memory.id, False)

            recalled_after_disable = service.search(
                db,
                user_id=self.user_id,
                session_id=self.session_ids[1],
                query="我喜欢什么饮品？",
            )
            self.assertEqual(0, recalled_after_disable["injected_count"])

            deleted = service.delete(db, correction["id"])
            self.assertTrue(deleted["deleted"])
            self.assertIsNone(db.get(LongTermMemory, correction["id"]))

            persisted_source = db.get(ConversationMessage, source_message.id)
            self.assertIsNotNone(persisted_source)
            self.assertEqual(question, persisted_source.content)

    def _clear_test_redis_keys(self) -> None:
        try:
            client = MemoryService(self.settings).short_term.client
            keys = [
                f"agent_loop:memory_version:{self.user_id}",
                *(f"agent_loop:sessions:{session_id}" for session_id in self.session_ids),
            ]
            keys.extend(client.scan_iter(match=f"agent_loop:memory_cache:{self.user_id}:*"))
            if keys:
                client.delete(*keys)
        except RedisError:
            # Redis 故障不应掩盖 PostgreSQL 长期记忆测试结果。
            pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
