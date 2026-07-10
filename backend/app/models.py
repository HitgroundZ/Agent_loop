from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector

from app.database import Base


def utcnow() -> datetime:       # 获取当前 UTC 时间
    return datetime.now(timezone.utc)


def new_id() -> str:            # 生成 uuid
    return str(uuid4())


class Document(Base):
    """
    文档表
    """
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(160), nullable=False)
    file_ext: Mapped[str] = mapped_column(String(20), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)       # 防止同一个源文件重复入库
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="uploaded")                 # 文档状态(uploaded, parsing, chunked, embedding, indexed, failed)
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False, default="default", index=True)
    workspace_id: Mapped[str] = mapped_column(String(80), nullable=False, default="default", index=True)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    permissions: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    parser_name: Mapped[str | None] = mapped_column(String(80), nullable=True)                          # 用哪个解析器解析的(markdown_parser, pdf_parser...)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    versions: Mapped[list["DocumentVersion"]] = relationship(                   # 文件和版本是一对多关系 List (一个文件可以有多个版本)
        back_populates="document",                                              # 指向外键 document
        cascade="all, delete-orphan",                                           # 可被级联处理，删除孤儿对象
    )
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )
    embedding_jobs: Mapped[list["EmbeddingJob"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )


class DocumentVersion(Base):
    """
    文档版本表，如果后续相同的文档进行了修改，就在其版本上进行自增
    """
    __tablename__ = "document_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)    # 外键，指向哪一个文档
    version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_text_object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    document: Mapped[Document] = relationship(back_populates="versions")        # 一个模型版本只对应一个文档
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="version",
        cascade="all, delete-orphan",
    )
    embedding_jobs: Mapped[list["EmbeddingJob"]] = relationship(
        back_populates="version",
        cascade="all, delete-orphan",
    )


class DocumentChunk(Base):
    """
    文档切片表，保存原文片段、元数据和向量。
    """
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("version_id", "chunk_index", name="uq_document_chunks_version_chunk_index"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    heading: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False, default="default", index=True)
    workspace_id: Mapped[str] = mapped_column(String(80), nullable=False, default="default", index=True)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    permissions: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    embedding_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    embedding_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    embedding_dim: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    document: Mapped[Document] = relationship(back_populates="chunks")
    version: Mapped[DocumentVersion] = relationship(back_populates="chunks")


class EmbeddingJob(Base):
    """
    embedding 异步任务，使用幂等键避免重复创建同一批向量化任务。
    """
    __tablename__ = "embedding_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(220), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    embedding_model: Mapped[str] = mapped_column(String(120), nullable=False)
    embedding_dim: Mapped[int] = mapped_column(Integer, nullable=False, default=1024)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    document: Mapped[Document] = relationship(back_populates="embedding_jobs")
    version: Mapped[DocumentVersion] = relationship(back_populates="embedding_jobs")


class IdempotencyRecord(Base):
    """
    幂等键，如果同一个操作执行多次，结果应该相同
    """
    __tablename__ = "idempotency_records"

    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    scope: Mapped[str] = mapped_column(String(80), nullable=False)              # 幂等键的作用域，例如：document_upload, chat_request, approval_submit
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)           # 第一次请求的 HTTP 状态码
    response_json: Mapped[dict] = mapped_column(JSON, nullable=False)           # 第一次请求返回的响应内容，如果用户反复请求，这里可以直接返回之前的响应
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
