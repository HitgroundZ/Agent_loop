"""Day 5/6 tool routing, risk policy and approval actions.

Revision ID: 202607070001
Revises: 202607060001
Create Date: 2026-07-07 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "202607070001"
down_revision: Union[str, None] = "202607060001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    return inspect(op.get_bind()).has_table(table_name)


def _column_exists(table_name: str, column_name: str) -> bool:
    if not _table_exists(table_name):
        return False
    return any(column["name"] == column_name for column in inspect(op.get_bind()).get_columns(table_name))


def upgrade() -> None:
    if not _column_exists("agent_runs", "retrieval_mode"):
        op.add_column(
            "agent_runs",
            sa.Column("retrieval_mode", sa.String(length=12), nullable=False, server_default="auto"),
        )
    if not _column_exists("agent_runs", "routing_decision"):
        op.add_column(
            "agent_runs",
            sa.Column(
                "routing_decision",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
        )
    if not _column_exists("agent_runs", "continuation_context"):
        op.add_column(
            "agent_runs",
            sa.Column(
                "continuation_context",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
        )

    if not _table_exists("tool_actions"):
        op.create_table(
            "tool_actions",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("run_id", sa.String(length=36), nullable=False),
            sa.Column("tool_call_id", sa.String(length=120), nullable=False),
            sa.Column("tool_name", sa.String(length=80), nullable=False),
            sa.Column("arguments", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("arguments_summary", sa.Text(), nullable=False, server_default=""),
            sa.Column("execution_context", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("permission", sa.String(length=80), nullable=False),
            sa.Column("risk_level", sa.String(length=20), nullable=False),
            sa.Column("side_effect", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="proposed"),
            sa.Column("reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("authorization_source", sa.String(length=40), nullable=False, server_default="user_message"),
            sa.Column("authorization_evidence", sa.Text(), nullable=False, server_default=""),
            sa.Column("requested_by", sa.String(length=80), nullable=False),
            sa.Column("approved_by", sa.String(length=80), nullable=True),
            sa.Column("decision_reason", sa.Text(), nullable=True),
            sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="10"),
            sa.Column("max_retries", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("run_id", "tool_call_id", name="uq_tool_actions_run_tool_call"),
        )
        for name, columns in (
            ("ix_tool_actions_run_id", ["run_id"]),
            ("ix_tool_actions_tool_name", ["tool_name"]),
            ("ix_tool_actions_risk_level", ["risk_level"]),
            ("ix_tool_actions_status", ["status"]),
        ):
            op.create_index(name, "tool_actions", columns)

    if not _table_exists("tool_outbox"):
        op.create_table(
            "tool_outbox",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("action_id", sa.String(length=36), nullable=False),
            sa.Column("channel", sa.String(length=40), nullable=False),
            sa.Column("recipient", sa.String(length=255), nullable=False),
            sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["action_id"], ["tool_actions.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("action_id", name="uq_tool_outbox_action_id"),
        )
        op.create_index("ix_tool_outbox_action_id", "tool_outbox", ["action_id"], unique=True)
        op.create_index("ix_tool_outbox_status", "tool_outbox", ["status"])


def downgrade() -> None:
    if _table_exists("tool_outbox"):
        op.drop_table("tool_outbox")
    if _table_exists("tool_actions"):
        op.drop_table("tool_actions")
    for column_name in ("continuation_context", "routing_decision", "retrieval_mode"):
        if _column_exists("agent_runs", column_name):
            op.drop_column("agent_runs", column_name)
