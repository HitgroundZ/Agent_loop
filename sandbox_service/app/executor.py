from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import logging
import threading
from typing import Any

import docker
from docker.errors import APIError, ImageNotFound, NotFound
from docker.types import Ulimit
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import ReadTimeout

from app.config import Settings
from app.policy import CommandPolicy


LOGGER = logging.getLogger(__name__)
SANDBOX_LABEL = "com.agent-loop.sandbox"


class SandboxBusy(RuntimeError):
    pass


class SandboxUnavailable(RuntimeError):
    pass


class SandboxExecutor:
    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self.settings = settings
        self.policy = CommandPolicy(settings)
        self._client = client
        self._semaphore = threading.BoundedSemaphore(max(1, settings.max_concurrency))

    @property
    def client(self):
        if self._client is None:
            self._client = docker.from_env(timeout=self.settings.docker_api_timeout_seconds)
        return self._client

    def health(self) -> dict[str, Any]:
        try:
            ping = bool(self.client.ping())
            image = self.client.images.get(self.settings.runtime_image)
        except (APIError, ImageNotFound, OSError) as exc:
            raise SandboxUnavailable(_safe_error(exc)) from exc
        return {
            "status": "ok" if ping else "unavailable",
            "docker": ping,
            "runtime_image": self.settings.runtime_image,
            "runtime_image_id": getattr(image, "short_id", None) or getattr(image, "id", ""),
        }

    def cleanup_stale_containers(self) -> int:
        try:
            containers = self.client.containers.list(
                all=True, filters={"label": f"{SANDBOX_LABEL}=true"}
            )
        except (APIError, OSError) as exc:
            LOGGER.warning("无法扫描遗留沙箱容器：%s", _safe_error(exc))
            return 0
        removed = 0
        now = datetime.now(timezone.utc)
        for container in containers:
            created = _container_created_at(container)
            if created is not None and (now - created).total_seconds() < self.settings.stale_container_ttl_seconds:
                continue
            try:
                container.remove(force=True, v=True)
                removed += 1
            except (APIError, NotFound):
                LOGGER.warning("遗留沙箱容器清理失败：%s", getattr(container, "short_id", "unknown"))
        return removed

    def execute(self, execution_id: str, argv: list[str], requested_env: dict[str, str]) -> dict[str, Any]:
        decision = self.policy.validate(argv, requested_env)
        if not self._semaphore.acquire(blocking=False):
            raise SandboxBusy("沙箱并发数已达到上限")

        started_at = datetime.now(timezone.utc)
        container = None
        result: dict[str, Any] | None = None
        cleanup = {
            "container_removed": False,
            "temporary_filesystems_removed": False,
        }
        cleanup_error: str | None = None
        try:
            self._ensure_no_active_duplicate(execution_id)
            container = self.client.containers.create(
                image=self.settings.runtime_image,
                command=decision.argv,
                name=f"agent-loop-sandbox-{hashlib.sha256(execution_id.encode()).hexdigest()[:12]}",
                detach=True,
                auto_remove=False,
                environment=decision.environment,
                labels={SANDBOX_LABEL: "true", "com.agent-loop.execution-id": execution_id},
                network_mode="none",
                read_only=True,
                tmpfs={
                    "/workspace": (
                        f"rw,nosuid,nodev,noexec,size={self.settings.workspace_tmpfs_size},"
                        "uid=65532,gid=65532,mode=0700"
                    ),
                    "/tmp": (
                        f"rw,nosuid,nodev,noexec,size={self.settings.temp_tmpfs_size},"
                        "uid=65532,gid=65532,mode=0700"
                    ),
                },
                user="65532:65532",
                working_dir="/workspace",
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                privileged=False,
                mem_limit=self.settings.memory_limit,
                memswap_limit=self.settings.memory_limit,
                nano_cpus=self.settings.nano_cpus,
                pids_limit=self.settings.pids_limit,
                shm_size=self.settings.shm_size,
                ulimits=[Ulimit(name="nofile", soft=256, hard=256)],
                init=True,
                stdin_open=False,
                tty=False,
                restart_policy={"Name": "no"},
            )
            container.start()
            timed_out = False
            exit_code: int | None = None
            try:
                wait_result = container.wait(timeout=self.settings.execution_timeout_seconds)
                exit_code = int(wait_result.get("StatusCode", 1))
            except (ReadTimeout, RequestsConnectionError) as exc:
                if not _is_timeout_error(exc):
                    raise
                timed_out = True
                try:
                    container.kill()
                    wait_result = container.wait(timeout=2)
                    exit_code = int(wait_result.get("StatusCode", 137))
                except (APIError, ReadTimeout, RequestsConnectionError):
                    exit_code = 137

            stdout = _collect_log(container, stdout=True, stderr=False, limit=self.settings.max_output_bytes)
            stderr = _collect_log(container, stdout=False, stderr=True, limit=self.settings.max_output_bytes)
            finished_at = datetime.now(timezone.utc)
            execution_status = "timed_out" if timed_out else (
                "succeeded" if exit_code == 0 else "nonzero_exit"
            )
            result = {
                "execution_id": execution_id,
                "execution_status": execution_status,
                "exit_code": exit_code,
                "timed_out": timed_out,
                "stdout": stdout["text"],
                "stderr": stderr["text"],
                "stdout_bytes": stdout["bytes"],
                "stderr_bytes": stderr["bytes"],
                "stdout_sha256": stdout["sha256"],
                "stderr_sha256": stderr["sha256"],
                "stdout_truncated": stdout["truncated"],
                "stderr_truncated": stderr["truncated"],
                "duration_ms": max(0, int((finished_at - started_at).total_seconds() * 1000)),
                "container_id": getattr(container, "short_id", "") or getattr(container, "id", "")[:12],
                "image": self.settings.runtime_image,
                "policy": decision.payload(),
                "limits": {
                    "timeout_seconds": self.settings.execution_timeout_seconds,
                    "memory": self.settings.memory_limit,
                    "nano_cpus": self.settings.nano_cpus,
                    "pids": self.settings.pids_limit,
                    "network": "none",
                },
                "cleanup": cleanup,
            }
        except (ImageNotFound, APIError, OSError) as exc:
            raise SandboxUnavailable(_safe_error(exc)) from exc
        finally:
            if container is not None:
                try:
                    container.remove(force=True, v=True)
                    cleanup["container_removed"] = True
                    cleanup["temporary_filesystems_removed"] = True
                except NotFound:
                    cleanup["container_removed"] = True
                    cleanup["temporary_filesystems_removed"] = True
                except APIError as exc:
                    cleanup_error = _safe_error(exc)
            self._semaphore.release()

        if result is None:
            raise SandboxUnavailable("沙箱执行没有产生结果")
        if cleanup_error:
            result["cleanup"]["error"] = cleanup_error
        return result

    def _ensure_no_active_duplicate(self, execution_id: str) -> None:
        containers = self.client.containers.list(
            all=True, filters={"label": f"com.agent-loop.execution-id={execution_id}"}
        )
        if containers:
            raise SandboxBusy("同一 execution_id 已存在沙箱容器")


def _collect_log(container: Any, *, stdout: bool, stderr: bool, limit: int) -> dict[str, Any]:
    digest = hashlib.sha256()
    buffer = bytearray()
    total = 0
    stream = container.logs(stdout=stdout, stderr=stderr, stream=True, follow=False)
    if stream is None:
        stream = []
    if isinstance(stream, (bytes, bytearray)):
        stream = [bytes(stream)]
    for chunk in stream:
        raw = bytes(chunk or b"")
        digest.update(raw)
        total += len(raw)
        if len(buffer) < limit:
            buffer.extend(raw[: max(0, limit - len(buffer))])
    return {
        "text": bytes(buffer).decode("utf-8", errors="replace"),
        "bytes": total,
        "sha256": digest.hexdigest(),
        "truncated": total > limit,
    }


def _container_created_at(container: Any) -> datetime | None:
    try:
        container.reload()
        raw = str((container.attrs or {}).get("Created") or "")
        if not raw:
            return None
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (APIError, ValueError, TypeError):
        return None


def _safe_error(exc: Exception) -> str:
    message = str(exc).replace("\n", " ").strip()
    return message[:500] or exc.__class__.__name__


def _is_timeout_error(exc: Exception) -> bool:
    current: BaseException | None = exc
    for _ in range(6):
        if current is None:
            break
        if isinstance(current, (TimeoutError, ReadTimeout)) or "timed out" in str(current).lower():
            return True
        current = current.__cause__ or current.__context__
    return False
