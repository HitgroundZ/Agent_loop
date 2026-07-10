"""Day 3 retrieval filters and full-text search.

Revision ID: 202607040001
Revises: 202607030002
Create Date: 2026-07-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "202607040001"
down_revision: Union[str, None] = "202607030002"
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
    if _table_exists("documents"):
        _add_filter_columns("documents")
        for index_name, columns in (
            ("ix_documents_tenant_id", ["tenant_id"]),
            ("ix_documents_workspace_id", ["workspace_id"]),
        ):
            if not _index_exists("documents", index_name):
                op.create_index(index_name, "documents", columns)
        op.execute("CREATE INDEX IF NOT EXISTS ix_documents_tags_gin ON documents USING gin (tags)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_documents_permissions_gin ON documents USING gin (permissions)")

    if _table_exists("document_chunks"):
        _add_filter_columns("document_chunks")
        for index_name, columns in (
            ("ix_document_chunks_tenant_id", ["tenant_id"]),
            ("ix_document_chunks_workspace_id", ["workspace_id"]),
        ):
            if not _index_exists("document_chunks", index_name):
                op.create_index(index_name, "document_chunks", columns)
        op.execute("CREATE INDEX IF NOT EXISTS ix_document_chunks_tags_gin ON document_chunks USING gin (tags)")
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_document_chunks_permissions_gin "
            "ON document_chunks USING gin (permissions)"
        )

        if not _column_exists("document_chunks", "search_vector"):
            op.execute(
                """
                ALTER TABLE document_chunks
                ADD COLUMN search_vector tsvector
                GENERATED ALWAYS AS (
                    to_tsvector('simple', coalesce(heading, '') || ' ' || coalesce(text, ''))
                ) STORED
                """
            )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_document_chunks_search_vector "
            "ON document_chunks USING gin (search_vector)"
        )


def downgrade() -> None:
    if _table_exists("document_chunks"):
        op.execute("DROP INDEX IF EXISTS ix_document_chunks_search_vector")
        if _column_exists("document_chunks", "search_vector"):
            op.drop_column("document_chunks", "search_vector")
        op.execute("DROP INDEX IF EXISTS ix_document_chunks_permissions_gin")
        op.execute("DROP INDEX IF EXISTS ix_document_chunks_tags_gin")
        for index_name in ("ix_document_chunks_workspace_id", "ix_document_chunks_tenant_id"):
            if _index_exists("document_chunks", index_name):
                op.drop_index(index_name, table_name="document_chunks")
        _drop_filter_columns("document_chunks")

    if _table_exists("documents"):
        op.execute("DROP INDEX IF EXISTS ix_documents_permissions_gin")
        op.execute("DROP INDEX IF EXISTS ix_documents_tags_gin")
        for index_name in ("ix_documents_workspace_id", "ix_documents_tenant_id"):
            if _index_exists("documents", index_name):
                op.drop_index(index_name, table_name="documents")
        _drop_filter_columns("documents")


def _add_filter_columns(table_name: str) -> None:
    if not _column_exists(table_name, "tenant_id"):
        op.add_column(
            table_name,
            sa.Column("tenant_id", sa.String(length=80), nullable=False, server_default="default"),
        )
    if not _column_exists(table_name, "workspace_id"):
        op.add_column(
            table_name,
            sa.Column("workspace_id", sa.String(length=80), nullable=False, server_default="default"),
        )
    if not _column_exists(table_name, "tags"):
        op.add_column(
            table_name,
            sa.Column(
                "tags",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
        )
    if not _column_exists(table_name, "permissions"):
        op.add_column(
            table_name,
            sa.Column(
                "permissions",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
        )


def _drop_filter_columns(table_name: str) -> None:
    for column_name in ("permissions", "tags", "workspace_id", "tenant_id"):
        if _column_exists(table_name, column_name):
            op.drop_column(table_name, column_name)
