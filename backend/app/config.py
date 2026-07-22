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
    agent_rate_limit_requests: int = 60
    agent_rate_limit_window_seconds: int = 60
    agent_token_budget: int = 12000
    agent_tool_max_rounds: int = 3
    agent_llm_timeout_seconds: float = 30.0
    memory_retrieval_limit: int = 5                                                                 # 最多选择多少条记忆
    memory_candidate_limit: int = 200                                                               # 最多从数据库取多少候选记忆
    memory_context_max_chars: int = 2400                                                            # 最多注入多少字符
    memory_cache_ttl_seconds: int = 5 * 60
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
    dashscope_http_base_url: str = "https://dashscope.aliyuncs.com/api/v1"
    llm_model: str = "qwen3.7-max"
    rerank_model: str = "qwen3-rerank"
    rerank_min_score: float = 0.35
    rerank_timeout_seconds: float = 15.0
    embedding_model: str = "text-embedding-v4"
    embedding_dim: int = 1024
    embedding_batch_size: int = 10
    chunk_max_chars: int = 1800
    chunk_overlap_chars: int = 200
    tool_default_roles: str = "user"
    tool_role_assignments: str = '{"demo-user":["operator","approver"],"day10-eval-*":["operator","approver"]}'
    tool_webhook_allowed_hosts: str = ""
    tool_webhook_timeout_seconds: float = 10.0
    tool_webhook_max_response_bytes: int = 64 * 1024
    sandbox_service_url: str = "http://sandbox-service:8080"
    sandbox_service_token: str = "local-sandbox-token-change-me"
    sandbox_request_timeout_seconds: float = 10.0
    sandbox_allowed_env_keys: str = "LANG,LC_ALL,TZ,PYTHONUNBUFFERED"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 跨域属性
    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def tool_default_role_list(self) -> list[str]:
        return [role.strip() for role in self.tool_default_roles.split(",") if role.strip()]

    @property
    def tool_webhook_allowed_host_list(self) -> list[str]:
        return [host.strip().lower() for host in self.tool_webhook_allowed_hosts.split(",") if host.strip()]

    @property
    def sandbox_allowed_env_key_set(self) -> set[str]:
        return {
            key.strip().upper()
            for key in self.sandbox_allowed_env_keys.split(",")
            if key.strip()
        }

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
