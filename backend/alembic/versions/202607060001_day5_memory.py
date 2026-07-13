"""Day 5 short and long term memory.

Revision ID: 202607060001
Revises: 202607050001
Create Date: 2026-07-06 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "202607060001"
down_revision: Union[str, None] = "202607050001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    return inspect(op.get_bind()).has_table(table_name)


def _column_exists(table_name: str, column_name: str) -> bool:
    if not _table_exists(table_name):
        return False
    return any(column["name"] == column_name for column in inspect(op.get_bind()).get_columns(table_name))


def _index_exists(table_name: str, index_name: str) -> bool:
    if not _table_exists(table_name):
        return False
    return any(index["name"] == index_name for index in inspect(op.get_bind()).get_indexes(table_name))


def upgrade() -> None:
    if not _column_exists("agent_runs", "user_id"):
        op.add_column(
            "agent_runs",
            sa.Column("user_id", sa.String(length=80), nullable=False, server_default="default"),
        )
    if not _column_exists("agent_runs", "memory_context"):
        op.add_column(
            "agent_runs",
            sa.Column(
                "memory_context",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
        )
    if not _index_exists("agent_runs", "ix_agent_runs_user_id"):
        op.create_index("ix_agent_runs_user_id", "agent_runs", ["user_id"])

    if not _table_exists("conversation_messages"):
        op.create_table(
            "conversation_messages",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=80), nullable=False),
            sa.Column("session_id", sa.String(length=80), nullable=False),
            sa.Column("run_id", sa.String(length=36), nullable=False),
            sa.Column("role", sa.String(length=20), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
    for index_name, columns in (
        ("ix_conversation_messages_user_id", ["user_id"]),
        ("ix_conversation_messages_session_id", ["session_id"]),
        ("ix_conversation_messages_run_id", ["run_id"]),
        ("ix_conversation_messages_role", ["role"]),
    ):
        if not _index_exists("conversation_messages", index_name):
            op.create_index(index_name, "conversation_messages", columns)

    if not _table_exists("long_term_memories"):
        op.create_table(
            "long_term_memories",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=80), nullable=False),
            sa.Column("category", sa.String(length=40), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            # 来源 ID 刻意不设外键；即使源文档之后删除，追溯标识仍会保留。
            sa.Column("source_message_id", sa.String(length=36), nullable=True),
            sa.Column("source_document_id", sa.String(length=36), nullable=True),
            sa.Column("parent_memory_id", sa.String(length=36), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column(
                "metadata",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column("access_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
    for index_name, columns in (
        ("ix_long_term_memories_user_id", ["user_id"]),
        ("ix_long_term_memories_category", ["category"]),
        ("ix_long_term_memories_source_message_id", ["source_message_id"]),
        ("ix_long_term_memories_source_document_id", ["source_document_id"]),
        ("ix_long_term_memories_parent_memory_id", ["parent_memory_id"]),
        ("ix_long_term_memories_enabled", ["enabled"]),
        ("ix_long_term_memories_user_enabled", ["user_id", "enabled"]),
    ):
        if not _index_exists("long_term_memories", index_name):
            op.create_index(index_name, "long_term_memories", columns)


def downgrade() -> None:
    if _table_exists("long_term_memories"):
        op.drop_table("long_term_memories")
    if _table_exists("conversation_messages"):
        op.drop_table("conversation_messages")
    if _index_exists("agent_runs", "ix_agent_runs_user_id"):
        op.drop_index("ix_agent_runs_user_id", table_name="agent_runs")
    if _column_exists("agent_runs", "memory_context"):
        op.drop_column("agent_runs", "memory_context")
    if _column_exists("agent_runs", "user_id"):
        op.drop_column("agent_runs", "user_id")
