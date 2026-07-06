from io import BytesIO
import logging

from minio import Minio
from minio.error import S3Error

from app.config import Settings


logger = logging.getLogger(__name__)


class ObjectStorage:
    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.minio_bucket
        self.client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )

    def ensure_bucket(self) -> None:
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)

    def put_bytes(self, object_key: str, content: bytes, content_type: str) -> None:
        self.ensure_bucket()
        self.client.put_object(
            self.bucket,
            object_key,
            BytesIO(content),
            length=len(content),
            content_type=content_type,
        )

    def put_text(self, object_key: str, text: str) -> None:
        self.put_bytes(object_key, text.encode("utf-8"), "text/plain; charset=utf-8")

    def delete_many(self, object_keys: list[str]) -> None:
        for object_key in {key for key in object_keys if key}:
            try:
                self.client.remove_object(self.bucket, object_key)
            except S3Error as exc:
                if exc.code not in {"NoSuchKey", "NoSuchBucket"}:
                    logger.warning("failed to delete MinIO object %s: %s", object_key, exc)


def get_object_storage(settings: Settings) -> ObjectStorage:
    return ObjectStorage(settings)
