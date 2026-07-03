"""Initial document ingestion schema.

Revision ID: 202607030001
Revises:
Create Date: 2026-07-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "202607030001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    return inspect(op.get_bind()).has_table(table_name)


def _index_exists(table_name: str, index_name: str) -> bool:
    return any(index["name"] == index_name for index in inspect(op.get_bind()).get_indexes(table_name))


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    if not _table_exists("documents"):
        op.create_table(
            "documents",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("filename", sa.String(length=255), nullable=False),
            sa.Column("content_type", sa.String(length=160), nullable=False),
            sa.Column("file_ext", sa.String(length=20), nullable=False),
            sa.Column("source_hash", sa.String(length=64), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("parser_name", sa.String(length=80), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("extracted_text", sa.Text(), nullable=True),
            sa.Column("text_preview", sa.Text(), nullable=True),
            sa.Column("metadata", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _index_exists("documents", "ix_documents_source_hash"):
        op.create_index("ix_documents_source_hash", "documents", ["source_hash"], unique=True)

    if not _table_exists("document_versions"):
        op.create_table(
            "document_versions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("document_id", sa.String(length=36), nullable=False),
            sa.Column("version_no", sa.Integer(), nullable=False),
            sa.Column("source_hash", sa.String(length=64), nullable=False),
            sa.Column("storage_path", sa.Text(), nullable=False),
            sa.Column("extracted_chars", sa.Integer(), nullable=False),
            sa.Column("metadata", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _index_exists("document_versions", "ix_document_versions_source_hash"):
        op.create_index("ix_document_versions_source_hash", "document_versions", ["source_hash"])

    if not _table_exists("idempotency_records"):
        op.create_table(
            "idempotency_records",
            sa.Column("key", sa.String(length=160), nullable=False),
            sa.Column("scope", sa.String(length=80), nullable=False),
            sa.Column("status_code", sa.Integer(), nullable=False),
            sa.Column("response_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("key"),
        )


def downgrade() -> None:
    if _table_exists("idempotency_records"):
        op.drop_table("idempotency_records")

    if _table_exists("document_versions"):
        if _index_exists("document_versions", "ix_document_versions_source_hash"):
            op.drop_index("ix_document_versions_source_hash", table_name="document_versions")
        op.drop_table("document_versions")

    if _table_exists("documents"):
        if _index_exists("documents", "ix_documents_source_hash"):
            op.drop_index("ix_documents_source_hash", table_name="documents")
        op.drop_table("documents")
