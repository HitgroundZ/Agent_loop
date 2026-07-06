"""Day 2 MinIO object keys, chunks, and embeddings.

Revision ID: 202607030002
Revises: 202607030001
Create Date: 2026-07-03 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
from pgvector.sqlalchemy import Vector
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "202607030002"
down_revision: Union[str, None] = "202607030001"
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
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    if _column_exists("documents", "extracted_text"):
        op.drop_column("documents", "extracted_text")

    if _table_exists("document_versions"):
        if not _column_exists("document_versions", "source_object_key"):
            op.add_column("document_versions", sa.Column("source_object_key", sa.Text(), nullable=True))
        if not _column_exists("document_versions", "extracted_text_object_key"):
            op.add_column("document_versions", sa.Column("extracted_text_object_key", sa.Text(), nullable=True))
        if _column_exists("document_versions", "storage_path"):
            op.drop_column("document_versions", "storage_path")

    if not _table_exists("document_chunks"):
        op.create_table(
            "document_chunks",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("document_id", sa.String(length=36), nullable=False),
            sa.Column("version_id", sa.String(length=36), nullable=False),
            sa.Column("chunk_index", sa.Integer(), nullable=False),
            sa.Column("page", sa.Integer(), nullable=True),
            sa.Column("heading", sa.Text(), nullable=True),
            sa.Column("source_hash", sa.String(length=64), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("metadata", sa.JSON(), nullable=False),
            sa.Column("embedding_status", sa.String(length=32), nullable=False),
            sa.Column("embedding_model", sa.String(length=120), nullable=True),
            sa.Column("embedding_dim", sa.Integer(), nullable=True),
            sa.Column("embedding", Vector(1024), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["version_id"], ["document_versions.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("version_id", "chunk_index", name="uq_document_chunks_version_chunk_index"),
        )

    for index_name, columns in (
        ("ix_document_chunks_document_id", ["document_id"]),
        ("ix_document_chunks_version_id", ["version_id"]),
        ("ix_document_chunks_source_hash", ["source_hash"]),
        ("ix_document_chunks_embedding_status", ["embedding_status"]),
    ):
        if not _index_exists("document_chunks", index_name):
            op.create_index(index_name, "document_chunks", columns)

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_document_chunks_embedding_hnsw "
        "ON document_chunks USING hnsw (embedding vector_cosine_ops) "
        "WHERE embedding IS NOT NULL"
    )

    if not _table_exists("embedding_jobs"):
        op.create_table(
            "embedding_jobs",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("document_id", sa.String(length=36), nullable=False),
            sa.Column("version_id", sa.String(length=36), nullable=False),
            sa.Column("idempotency_key", sa.String(length=220), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("max_attempts", sa.Integer(), nullable=False),
            sa.Column("embedding_model", sa.String(length=120), nullable=False),
            sa.Column("embedding_dim", sa.Integer(), nullable=False),
            sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["version_id"], ["document_versions.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    for index_name, columns, unique in (
        ("ix_embedding_jobs_document_id", ["document_id"], False),
        ("ix_embedding_jobs_version_id", ["version_id"], False),
        ("ix_embedding_jobs_idempotency_key", ["idempotency_key"], True),
        ("ix_embedding_jobs_status", ["status"], False),
    ):
        if not _index_exists("embedding_jobs", index_name):
            op.create_index(index_name, "embedding_jobs", columns, unique=unique)


def downgrade() -> None:
    if _table_exists("embedding_jobs"):
        op.drop_table("embedding_jobs")

    if _table_exists("document_chunks"):
        op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding_hnsw")
        op.drop_table("document_chunks")

    if _table_exists("document_versions"):
        if _column_exists("document_versions", "extracted_text_object_key"):
            op.drop_column("document_versions", "extracted_text_object_key")
        if _column_exists("document_versions", "source_object_key"):
            op.drop_column("document_versions", "source_object_key")
        if not _column_exists("document_versions", "storage_path"):
            op.add_column("document_versions", sa.Column("storage_path", sa.Text(), nullable=True))

    if _table_exists("documents") and not _column_exists("documents", "extracted_text"):
        op.add_column("documents", sa.Column("extracted_text", sa.Text(), nullable=True))
