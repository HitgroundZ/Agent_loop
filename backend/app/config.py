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
    upload_dir: str = "./storage/uploads"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"                               # 允许跨域的ip
    max_upload_bytes: int = 50 * 1024 * 1024

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
