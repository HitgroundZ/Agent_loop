"""RAG evaluation runs and case results.

Revision ID: 202607220001
Revises: 202607070001
Create Date: 2026-07-22 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "202607220001"
down_revision: Union[str, None] = "202607070001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    return inspect(op.get_bind()).has_table(table_name)


def upgrade() -> None:
    if not _table_exists("evaluation_runs"):
        op.create_table(
            "evaluation_runs",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("dataset_id", sa.String(length=120), nullable=False),
            sa.Column("dataset_version", sa.String(length=80), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
            sa.Column("strategy", sa.String(length=20), nullable=False, server_default="hybrid"),
            sa.Column("top_k", sa.Integer(), nullable=False, server_default="5"),
            sa.Column("judge_model", sa.String(length=120), nullable=False),
            sa.Column("embedding_model", sa.String(length=120), nullable=False),
            sa.Column("config_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("coverage", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("total_cases", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("completed_cases", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failed_cases", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_evaluation_runs_dataset_id", "evaluation_runs", ["dataset_id"])
        op.create_index("ix_evaluation_runs_status", "evaluation_runs", ["status"])
        op.create_index("ix_evaluation_runs_strategy", "evaluation_runs", ["strategy"])

    if not _table_exists("evaluation_case_results"):
        op.create_table(
            "evaluation_case_results",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("evaluation_run_id", sa.String(length=36), nullable=False),
            sa.Column("case_id", sa.String(length=120), nullable=False),
            sa.Column("agent_run_id", sa.String(length=36), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="completed"),
            sa.Column("question", sa.Text(), nullable=False),
            sa.Column("reference_answer", sa.Text(), nullable=False),
            sa.Column("answer", sa.Text(), nullable=False, server_default=""),
            sa.Column("reference_contexts", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("retrieved_contexts", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("scores", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("hit_at_k", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["evaluation_run_id"], ["evaluation_runs.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="SET NULL"),
            sa.UniqueConstraint("evaluation_run_id", "case_id", name="uq_evaluation_case_results_run_case"),
        )
        op.create_index("ix_evaluation_case_results_evaluation_run_id", "evaluation_case_results", ["evaluation_run_id"])
        op.create_index("ix_evaluation_case_results_case_id", "evaluation_case_results", ["case_id"])
        op.create_index("ix_evaluation_case_results_agent_run_id", "evaluation_case_results", ["agent_run_id"])
        op.create_index("ix_evaluation_case_results_status", "evaluation_case_results", ["status"])


def downgrade() -> None:
    if _table_exists("evaluation_case_results"):
        op.drop_table("evaluation_case_results")
    if _table_exists("evaluation_runs"):
        op.drop_table("evaluation_runs")
