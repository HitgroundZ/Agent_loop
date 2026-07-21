from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings


class SandboxServiceError(RuntimeError):
    pass


class SandboxPolicyRejected(RuntimeError):
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        super().__init__(str(payload.get("message") or "沙箱策略拒绝了该命令"))


class SandboxClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def execute(
        self,
        *,
        execution_id: str,
        argv: list[str],
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.settings.sandbox_service_url.rstrip('/')}/v1/executions"
        try:
            response = httpx.post(
                url,
                json={"execution_id": execution_id, "argv": argv, "env": env or {}},
                headers={"Authorization": f"Bearer {self.settings.sandbox_service_token}"},
                timeout=self.settings.sandbox_request_timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise SandboxServiceError("sandbox service 请求超时，命令结果不确定") from exc
        except httpx.HTTPError as exc:
            raise SandboxServiceError("无法连接 sandbox service") from exc

        payload = _response_payload(response)
        if response.status_code == 403:
            detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else payload
            raise SandboxPolicyRejected(detail if isinstance(detail, dict) else {"message": str(detail)})
        if response.status_code >= 400:
            detail = payload.get("detail") or payload.get("message") or f"HTTP {response.status_code}"
            if isinstance(detail, dict):
                detail = detail.get("message") or detail.get("code") or "sandbox service 错误"
            raise SandboxServiceError(f"sandbox service 执行失败：{str(detail)[:300]}")
        if not isinstance(payload, dict) or not payload.get("execution_status"):
            raise SandboxServiceError("sandbox service 返回了无效结果")
        return payload


def _response_payload(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {"message": response.text[:300]}
    return payload if isinstance(payload, dict) else {"message": str(payload)[:300]}

