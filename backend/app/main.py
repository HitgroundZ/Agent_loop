from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import get_settings
from app.database import SessionLocal
from app.routers.agent import router as agent_router
from app.routers.documents import router as documents_router
from app.routers.memories import router as memories_router
from app.routers.retrieval import router as retrieval_router
from app.routers.tool_actions import router as tool_actions_router


settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,                # 允许跨域
    allow_credentials=True,                                 # 允许前端请求携带认证信息(Cookie, Authorization header, TLS client certificate)
    allow_methods=["*"],                                    # 允许所有操作方式的请求
    allow_headers=["*"],                                    # 允许前端携带头文件
)

# 挂载路由
app.include_router(documents_router)
app.include_router(retrieval_router)
app.include_router(agent_router)
app.include_router(memories_router)
app.include_router(tool_actions_router)

# 健康状态测试路由
@app.get("/api/health")
def health() -> dict:
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
    return {"status": "ok", "service": "backend"}
