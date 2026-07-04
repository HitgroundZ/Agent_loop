from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

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
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="uploaded")                 # 文档状态(uploaded, parsing, parsed, failed, indexed)
    parser_name: Mapped[str | None] = mapped_column(String(80), nullable=True)                          # 用哪个解析器解析的(markdown_parser, pdf_parser...)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    versions: Mapped[list["DocumentVersion"]] = relationship(                   # 文件和版本是一对多关系 List (一个文件可以有多个版本)
        back_populates="document",                                              # 指向外键 document
        cascade="all, delete-orphan",                                           # 可被级联处理，删除孤儿对象
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
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    extracted_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    document: Mapped[Document] = relationship(back_populates="versions")        # 一个模型版本只对应一个文档


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

