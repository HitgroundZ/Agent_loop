from __future__ import annotations

import hmac
import logging

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.executor import SandboxBusy, SandboxExecutor, SandboxUnavailable
from app.policy import PolicyRejected


LOGGER = logging.getLogger(__name__)
settings = get_settings()
executor = SandboxExecutor(settings)
app = FastAPI(title="Agent Loop Sandbox Service", version="0.1.0")


class ExecutionRequest(BaseModel):
    execution_id: str = Field(min_length=1, max_length=120)
    argv: list[str] = Field(min_length=1, max_length=64)
    env: dict[str, str] = Field(default_factory=dict)


@app.on_event("startup")
def cleanup_stale_sandboxes() -> None:
    removed = executor.cleanup_stale_containers()
    if removed:
        LOGGER.warning("启动时清理了 %s 个遗留沙箱容器", removed)


@app.get("/health")
def health() -> dict:
    try:
        return executor.health()
    except SandboxUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@app.post("/v1/executions")
def execute_command(
    payload: ExecutionRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    _require_token(authorization)
    try:
        return executor.execute(payload.execution_id, payload.argv, payload.env)
    except PolicyRejected as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=exc.payload()) from exc
    except SandboxBusy as exc:
        code = status.HTTP_409_CONFLICT if "execution_id" in str(exc) else status.HTTP_429_TOO_MANY_REQUESTS
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    except SandboxUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


def _require_token(authorization: str | None) -> None:
    expected = f"Bearer {settings.service_token}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="sandbox service token 无效")
