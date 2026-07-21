from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from app.config import Settings


ALLOWED_COMMANDS = {
    "python": "python-runtime",
    "python3": "python-runtime",
    "echo": "text-output",
    "printf": "text-output",
    "pwd": "workspace-inspection",
    "ls": "workspace-inspection",
    "cat": "workspace-read",
    "head": "workspace-read",
    "tail": "workspace-read",
    "grep": "workspace-read",
    "wc": "workspace-read",
    "sort": "text-transform",
    "uniq": "text-transform",
    "sleep": "timeout-test",
}

DENIED_COMMANDS = {
    "sh", "bash", "ash", "zsh", "dash",
    "rm", "rmdir", "mv", "cp", "ln",
    "mount", "umount", "dd", "mkfs", "fdisk",
    "chmod", "chown", "su", "sudo", "kill", "pkill", "killall",
    "reboot", "shutdown", "poweroff",
    "curl", "wget", "nc", "netcat", "ssh", "scp", "sftp",
    "docker", "dockerd", "podman", "nerdctl",
    "apk", "apt", "apt-get", "yum", "dnf", "pip", "pip3",
}

SENSITIVE_ENV_PATTERN = re.compile(
    r"(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH|COOKIE)", re.IGNORECASE
)
ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
FORBIDDEN_ARGUMENT_MARKERS = (
    "/var/run/docker.sock", "/run/docker.sock", "--privileged",
    "pid=host", "network=host", "ipc=host", "uts=host",
)
FORBIDDEN_PYTHON_MARKERS = (
    "os.system(", "os.popen(", "subprocess.", "pty.spawn(",
)


class PolicyRejected(ValueError):
    def __init__(self, code: str, message: str, *, rule: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.rule = rule

    def payload(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "rule": self.rule}


@dataclass(frozen=True)
class PolicyDecision:
    argv: list[str]
    environment: dict[str, str]
    rule: str
    requested_env_keys: list[str]

    def payload(self) -> dict[str, Any]:
        return {
            "decision": "allowed",
            "rule": self.rule,
            "requested_env_keys": self.requested_env_keys,
        }


class CommandPolicy:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def validate(self, argv: list[str], requested_env: dict[str, str]) -> PolicyDecision:
        normalized = self._validate_argv(argv)
        environment, requested_keys = self._validate_environment(requested_env)
        executable = normalized[0]
        return PolicyDecision(
            argv=normalized,
            environment=environment,
            rule=ALLOWED_COMMANDS[executable],
            requested_env_keys=requested_keys,
        )

    def _validate_argv(self, argv: list[str]) -> list[str]:
        if not isinstance(argv, list) or not argv:
            raise PolicyRejected("invalid_argv", "argv 必须是非空字符串数组", rule="argv-shape")
        if len(argv) > 64:
            raise PolicyRejected("argv_too_long", "argv 最多包含 64 项", rule="argv-shape")
        if any(not isinstance(item, str) or not item for item in argv):
            raise PolicyRejected("invalid_argv", "argv 每一项都必须是非空字符串", rule="argv-shape")
        if any(len(item) > 4096 for item in argv) or sum(len(item) for item in argv) > 16 * 1024:
            raise PolicyRejected("argv_too_long", "argv 总长度超过安全上限", rule="argv-size")
        if any(
            any((ord(char) < 32 and char not in "\n\r\t") or ord(char) == 127 for char in item)
            for item in argv
        ):
            raise PolicyRejected("control_character", "argv 不允许控制字符", rule="argv-encoding")

        executable = argv[0]
        if "/" in executable or "\\" in executable:
            raise PolicyRejected("command_denied", "不允许通过路径选择可执行文件", rule="executable-path")
        if executable in DENIED_COMMANDS:
            raise PolicyRejected("command_denied", f"命令 {executable} 位于拒绝列表", rule="denylist")
        if executable not in ALLOWED_COMMANDS:
            raise PolicyRejected("command_not_allowed", f"命令 {executable} 不在允许列表", rule="allowlist")

        joined = " ".join(argv).lower()
        if any(marker in joined for marker in FORBIDDEN_ARGUMENT_MARKERS):
            raise PolicyRejected("escape_target_denied", "命令包含禁止访问的宿主或命名空间目标", rule="escape-defense")
        if executable in {"python", "python3"}:
            self._validate_python(argv)
        elif executable == "pwd" and len(argv) != 1:
            raise PolicyRejected("arguments_denied", "pwd 不接受额外参数", rule="command-arguments")
        elif executable == "sleep":
            self._validate_sleep(argv)
        elif executable in {"ls", "cat", "head", "tail", "grep", "wc", "sort", "uniq"}:
            self._validate_workspace_paths(argv[1:])
        return list(argv)

    @staticmethod
    def _validate_python(argv: list[str]) -> None:
        if len(argv) == 2 and argv[1] in {"--version", "-V"}:
            return
        if len(argv) != 3 or argv[1] != "-c":
            raise PolicyRejected(
                "arguments_denied",
                "Python 首版只允许 python -c <code> 或 --version",
                rule="python-arguments",
            )
        lowered = argv[2].lower().replace(" ", "")
        if any(marker.replace(" ", "") in lowered for marker in FORBIDDEN_PYTHON_MARKERS):
            raise PolicyRejected(
                "python_process_spawn_denied",
                "Python 代码不允许启动子进程或调用系统 shell",
                rule="python-process-defense",
            )

    @staticmethod
    def _validate_sleep(argv: list[str]) -> None:
        if len(argv) != 2:
            raise PolicyRejected("arguments_denied", "sleep 只接受一个秒数", rule="sleep-arguments")
        try:
            seconds = float(argv[1])
        except ValueError as exc:
            raise PolicyRejected("arguments_denied", "sleep 秒数必须是数字", rule="sleep-arguments") from exc
        if seconds < 0 or seconds > 60:
            raise PolicyRejected("arguments_denied", "sleep 秒数必须在 0 到 60 之间", rule="sleep-arguments")

    @staticmethod
    def _validate_workspace_paths(arguments: list[str]) -> None:
        for argument in arguments:
            if argument.startswith("-"):
                continue
            candidate = argument.replace("\\", "/")
            if candidate.startswith("/") and not (
                candidate == "/workspace" or candidate.startswith("/workspace/")
            ):
                raise PolicyRejected(
                    "path_denied", "文件参数只能访问 /workspace", rule="workspace-boundary"
                )
            if ".." in candidate.split("/"):
                raise PolicyRejected(
                    "path_denied", "文件参数不允许父目录跳转", rule="workspace-boundary"
                )

    def _validate_environment(self, requested_env: dict[str, str]) -> tuple[dict[str, str], list[str]]:
        if not isinstance(requested_env, dict):
            raise PolicyRejected("invalid_env", "env 必须是字符串映射", rule="env-shape")
        if len(requested_env) > 16:
            raise PolicyRejected("invalid_env", "env 最多包含 16 项", rule="env-shape")
        environment = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": "/workspace",
            "TMPDIR": "/tmp",
        }
        requested_keys: list[str] = []
        for key, value in requested_env.items():
            normalized_key = str(key).upper()
            if not ENV_KEY_PATTERN.fullmatch(str(key)) or not isinstance(value, str):
                raise PolicyRejected("invalid_env", "环境变量名称和值必须是字符串", rule="env-shape")
            if SENSITIVE_ENV_PATTERN.search(normalized_key):
                raise PolicyRejected(
                    "sensitive_env_denied", f"环境变量 {key} 可能包含凭证，禁止注入", rule="env-secret-defense"
                )
            if normalized_key not in self.settings.allowed_env_key_set:
                raise PolicyRejected(
                    "env_not_allowed", f"环境变量 {key} 不在白名单", rule="env-allowlist"
                )
            if len(value) > 1024 or any(ord(char) == 0 for char in value):
                raise PolicyRejected("invalid_env", f"环境变量 {key} 的值无效", rule="env-size")
            environment[normalized_key] = value
            requested_keys.append(normalized_key)
        return environment, sorted(requested_keys)
