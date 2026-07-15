from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import AgentTraceEvent, IdempotencyRecord, ToolAction
from app.services.agent_loop import AgentLoopService
from app.services.tooling import RolePolicy, ToolExecutor, action_payload


router = APIRouter(prefix="/api/tool-actions", tags=["tool-actions"])


class DecisionPayload(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


@router.get("")
def list_tool_actions(
    action_status: str | None = Query(default=None, alias="status"),
    risk_level: str | None = None,
    run_id: str | None = None,
    tool_name: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    principal_id: str = Header(alias="X-Principal-Id"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    _require_approver(settings, principal_id, "approval.read")
    conditions = []
    if action_status:
        conditions.append(ToolAction.status == action_status)
    if risk_level:
        conditions.append(ToolAction.risk_level == risk_level)
    if run_id:
        conditions.append(ToolAction.run_id == run_id)
    if tool_name:
        conditions.append(ToolAction.tool_name == tool_name)
    statement = select(ToolAction).where(*conditions)
    actions = list(db.scalars(statement.order_by(ToolAction.created_at.desc()).limit(limit)))
    total = db.scalar(select(func.count()).select_from(ToolAction).where(*conditions)) or 0
    return {"items": [action_payload(action) for action in actions], "count": total}


@router.get("/{action_id}")
def get_tool_action(
    action_id: str,
    principal_id: str = Header(alias="X-Principal-Id"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    _require_approver(settings, principal_id, "approval.read")
    action = db.get(ToolAction, action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="工具 action 不存在")
    payload = action_payload(action)
    traces = list(db.scalars(
        select(AgentTraceEvent)
        .where(AgentTraceEvent.run_id == action.run_id)
        .order_by(AgentTraceEvent.sequence)
    ))
    payload["trace"] = [
        {
            "sequence": event.sequence,
            "state": event.state,
            "output_summary": event.output_summary,
            "duration_ms": event.duration_ms,
            "error": event.error,
            "created_at": event.created_at.isoformat() if event.created_at else None,
        }
        for event in traces
    ]
    return payload


@router.post("/{action_id}/approve")
def approve_tool_action(
    action_id: str,
    payload: DecisionPayload,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal_id: str = Header(alias="X-Principal-Id"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    return _decide(
        db, settings, action_id=action_id, decision="approve",
        reason=payload.reason, idempotency_key=idempotency_key,
        principal_id=principal_id,
    )


@router.post("/{action_id}/reject")
def reject_tool_action(
    action_id: str,
    payload: DecisionPayload,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal_id: str = Header(alias="X-Principal-Id"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    return _decide(
        db, settings, action_id=action_id, decision="reject",
        reason=payload.reason, idempotency_key=idempotency_key,
        principal_id=principal_id,
    )


def _decide(
    db: Session,
    settings: Settings,
    *,
    action_id: str,
    decision: str,
    reason: str | None,
    idempotency_key: str | None,
    principal_id: str,
) -> JSONResponse:
    _require_approver(settings, principal_id, "approval.decide")
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="审批必须提供 Idempotency-Key")
    scope = f"tool_actions.{action_id}.{decision}"
    cached = _idempotent_response(db, idempotency_key, scope)
    if cached is not None:
        return cached

    action = db.scalar(select(ToolAction).where(ToolAction.id == action_id).with_for_update())
    if action is None:
        raise HTTPException(status_code=404, detail="工具 action 不存在")
    if action.status != "pending" and not (
        (decision == "approve" and action.status == "executed")
        or (decision == "reject" and action.status == "rejected")
    ):
        raise HTTPException(
            status_code=409,
            detail=f"action 已处于 {action.status}，不能执行 {decision} 决策",
        )

    if action.status == "pending":
        action.approved_by = principal_id
        action.decision_reason = (reason or "").strip()
        action.decided_at = datetime.now(timezone.utc)
        if decision == "approve":
            # 在持有行锁时先占有执行权；其他并发审批只能看到 running，不能重复执行。
            action.status = "running"
            db.add(action)
            db.commit()
            action = ToolExecutor(settings).execute(db, action)
        else:
            action.status = "rejected"
            action.result = {"rejected": True, "reason": action.decision_reason}
            db.add(action)
            db.commit()
            db.refresh(action)

    remaining = db.scalar(
        select(ToolAction.id).where(
            ToolAction.run_id == action.run_id,
            ToolAction.status == "pending",
        ).limit(1)
    )
    resumed_run = None
    if remaining is None:
        resumed_run = AgentLoopService(settings).resume(db, action.run_id)

    response_payload = {
        "action": action_payload(db.get(ToolAction, action.id)),
        "run": resumed_run,
        "resumed": resumed_run is not None,
    }
    db.merge(IdempotencyRecord(
        key=idempotency_key, scope=scope, status_code=200,
        response_json=response_payload,
    ))
    db.commit()
    return JSONResponse(status_code=200, content=response_payload)


def _require_approver(settings: Settings, principal_id: str, permission: str) -> None:
    try:
        RolePolicy(settings).require(principal_id, permission)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


def _idempotent_response(db: Session, key: str, scope: str) -> JSONResponse | None:
    record = db.get(IdempotencyRecord, key)
    if record is None:
        return None
    if record.scope != scope:
        raise HTTPException(status_code=409, detail="Idempotency-Key 已被其他操作使用")
    return JSONResponse(status_code=record.status_code, content=record.response_json)
