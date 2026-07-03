from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

# 基类，其他表的父类
class Base(DeclarativeBase):
    pass


settings = get_settings()

# 初始化Engine，创建数据库的链接
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
)
# 初始化会话工厂，用于后续的增删改查，db=SessionLocal()db.add
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)

# FastAPI的数据库操作方式，后续调用get_db 函数，每次调用都是一个独立的会话
def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
