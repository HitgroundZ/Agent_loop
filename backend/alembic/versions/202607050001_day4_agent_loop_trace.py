"""Day 4 agent loop state machine and trace events.

Revision ID: 202607050001
Revises: 202607040001
Create Date: 2026-07-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "202607050001"
down_revision: Union[str, None] = "202607040001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    return inspect(op.get_bind()).has_table(table_name)


def _index_exists(table_name: str, index_name: str) -> bool:
    if not _table_exists(table_name):
        return False
    return any(index["name"] == index_name for index in inspect(op.get_bind()).get_indexes(table_name))


def upgrade() -> None:
    if not _table_exists("agent_runs"):
        op.create_table(
            "agent_runs",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("session_id", sa.String(length=80), nullable=False),
            sa.Column("question", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="created"),
            sa.Column("retrieval_strategy", sa.String(length=20), nullable=False, server_default="hybrid"),
            sa.Column("answer", sa.Text(), nullable=True),
            sa.Column("citations", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("plan", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("retrieval_result", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("evaluation", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("token_usage", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    for index_name, columns in (
        ("ix_agent_runs_session_id", ["session_id"]),
        ("ix_agent_runs_status", ["status"]),
    ):
        if not _index_exists("agent_runs", index_name):
            op.create_index(index_name, "agent_runs", columns)

    if not _table_exists("agent_trace_events"):
        op.create_table(
            "agent_trace_events",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("run_id", sa.String(length=36), nullable=False),
            sa.Column("session_id", sa.String(length=80), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("state", sa.String(length=40), nullable=False),
            sa.Column("input_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("output_summary", sa.Text(), nullable=False, server_default=""),
            sa.Column("output_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("token_usage", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("run_id", "sequence", name="uq_agent_trace_events_run_sequence"),
        )

    for index_name, columns in (
        ("ix_agent_trace_events_run_id", ["run_id"]),
        ("ix_agent_trace_events_session_id", ["session_id"]),
        ("ix_agent_trace_events_state", ["state"]),
    ):
        if not _index_exists("agent_trace_events", index_name):
            op.create_index(index_name, "agent_trace_events", columns)


def downgrade() -> None:
    if _table_exists("agent_trace_events"):
        for index_name in (
            "ix_agent_trace_events_state",
            "ix_agent_trace_events_session_id",
            "ix_agent_trace_events_run_id",
        ):
            if _index_exists("agent_trace_events", index_name):
                op.drop_index(index_name, table_name="agent_trace_events")
        op.drop_table("agent_trace_events")

    if _table_exists("agent_runs"):
        for index_name in ("ix_agent_runs_status", "ix_agent_runs_session_id"):
            if _index_exists("agent_runs", index_name):
                op.drop_index(index_name, table_name="agent_runs")
        op.drop_table("agent_runs")
