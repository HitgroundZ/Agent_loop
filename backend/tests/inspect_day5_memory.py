"""只读输出 Day5 表、字段、索引、外键和 demo 数据。"""
from __future__ import annotations

import os

from sqlalchemy import create_engine, inspect, text


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://agent_loop:agent_loop@127.0.0.1:5432/agent_loop",
)
INSPECT_USER_ID = os.getenv("MEMORY_INSPECT_USER_ID", "day5-structure-demo")
TABLES = ("agent_runs", "conversation_messages", "long_term_memories")


def main() -> None:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    inspector = inspect(engine)
    try:
        for table_name in TABLES:
            print(f"\n=== {table_name} ===")
            print("columns:")
            for column in inspector.get_columns(table_name):
                print(
                    f"  {column['name']:<24} {str(column['type']):<28} "
                    f"nullable={column['nullable']} default={column.get('default')}"
                )
            print("indexes:")
            for index in inspector.get_indexes(table_name):
                print(
                    f"  {index['name']:<45} columns={index['column_names']} "
                    f"unique={index['unique']}"
                )
            print("foreign keys:")
            foreign_keys = inspector.get_foreign_keys(table_name)
            if not foreign_keys:
                print("  (none)")
            for foreign_key in foreign_keys:
                print(
                    f"  {foreign_key['constrained_columns']} -> "
                    f"{foreign_key['referred_table']}.{foreign_key['referred_columns']}"
                )

        with engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            print(f"\nAlembic revision: {revision}")
            rows = connection.execute(
                text(
                    """
                    SELECT id, category, enabled, source_message_id, source_document_id,
                           parent_memory_id, access_count, left(content, 80) AS content_preview
                    FROM long_term_memories
                    WHERE user_id = :user_id
                    ORDER BY created_at, category
                    """
                ),
                {"user_id": INSPECT_USER_ID},
            ).mappings()
            print(f"\nDemo memories for user_id={INSPECT_USER_ID}:")
            found = False
            for row in rows:
                found = True
                print(dict(row))
            if not found:
                print("  (none; run tests/seed_day5_memory_demo.py first)")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
