"""
基础参数信息
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Agent Loop Knowledge Base"
    database_url: str = "postgresql+psycopg://agent_loop:agent_loop@127.0.0.1:5432/agent_loop"      #链接容器里的postgre数据库
    redis_url: str = "redis://redis:6379/0"
    redis_embedding_queue: str = "agent_loop:embedding_jobs"
    agent_session_message_limit: int = 12
    agent_session_ttl_seconds: int = 24 * 60 * 60
    upload_dir: str = "./storage/uploads"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"                               # 允许跨域的ip
    max_upload_bytes: int = 50 * 1024 * 1024
    minio_endpoint: str = "127.0.0.1:9000"
    minio_access_key: str = "agent_loop"
    minio_secret_key: str = "agent_loop_password"
    minio_bucket: str = "agent-loop-documents"
    minio_secure: bool = False
    dashscope_api_key: str | None = None
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen3.7-max"
    rerank_model: str = "qwen3-vl-rerank"
    embedding_model: str = "text-embedding-v4"
    embedding_dim: int = 1024
    embedding_batch_size: int = 10
    chunk_max_chars: int = 1800
    chunk_overlap_chars: int = 200

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 跨域属性
    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    # 加载文件属性
    @property
    def upload_path(self) -> Path:
        path = Path(self.upload_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

# 在程序开始前就初始化 Settings参数信息，这样不用每次使用的时候都调用一遍
@lru_cache
def get_settings() -> Settings:
    return Settings()
