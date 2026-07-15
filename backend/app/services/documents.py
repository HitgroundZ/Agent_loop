from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Document
from app.services.storage import get_object_storage


def delete_document_resource(db: Session, settings: Settings, document_id: str) -> dict:
    """删除文档数据库记录与对象存储内容；目标不存在时返回幂等结果。"""
    document = db.get(Document, document_id)
    if document is None:
        return {"deleted": True, "already_deleted": True, "document_id": document_id}

    object_keys: list[str] = []
    for version in document.versions:
        if version.source_object_key:
            object_keys.append(version.source_object_key)
        if version.extracted_text_object_key:
            object_keys.append(version.extracted_text_object_key)

    db.delete(document)
    db.commit()
    get_object_storage(settings).delete_many(object_keys)
    return {"deleted": True, "already_deleted": False, "document_id": document_id}
