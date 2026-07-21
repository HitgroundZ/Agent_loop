from datetime import datetime, timezone
import unittest
from unittest.mock import Mock

from requests.exceptions import ReadTimeout

from app.config import Settings
from app.executor import SandboxExecutor


class SandboxExecutorTest(unittest.TestCase):
    def _client(self, container: Mock) -> Mock:
        client = Mock()
        client.containers.list.return_value = []
        client.containers.create.return_value = container
        return client

    def test_applies_limits_captures_logs_and_removes_container(self) -> None:
        container = Mock()
        container.short_id = "abc123"
        container.wait.return_value = {"StatusCode": 0}
        container.logs.side_effect = [[b"42\n"], [b""]]
        client = self._client(container)
        executor = SandboxExecutor(Settings(), client=client)

        result = executor.execute("action-1", ["python", "-c", "print(6*7)"], {})

        kwargs = client.containers.create.call_args.kwargs
        self.assertEqual("none", kwargs["network_mode"])
        self.assertTrue(kwargs["read_only"])
        self.assertEqual(["ALL"], kwargs["cap_drop"])
        self.assertFalse(kwargs["privileged"])
        self.assertEqual("128m", kwargs["mem_limit"])
        self.assertEqual(64, kwargs["pids_limit"])
        self.assertNotIn("volumes", kwargs)
        self.assertEqual("42\n", result["stdout"])
        self.assertEqual("succeeded", result["execution_status"])
        self.assertTrue(result["cleanup"]["container_removed"])
        container.remove.assert_called_once_with(force=True, v=True)

    def test_timeout_kills_then_removes_container(self) -> None:
        container = Mock()
        container.short_id = "timeout123"
        container.wait.side_effect = [ReadTimeout(), {"StatusCode": 137}]
        container.logs.side_effect = [[b"started\n"], [b""]]
        executor = SandboxExecutor(Settings(execution_timeout_seconds=1), client=self._client(container))

        result = executor.execute("action-timeout", ["sleep", "30"], {})

        self.assertTrue(result["timed_out"])
        self.assertEqual("timed_out", result["execution_status"])
        container.kill.assert_called_once()
        container.remove.assert_called_once_with(force=True, v=True)

    def test_large_output_is_bounded_and_hashed(self) -> None:
        container = Mock()
        container.short_id = "large123"
        container.wait.return_value = {"StatusCode": 0}
        container.logs.side_effect = [[b"1234", b"5678"], [b""]]
        executor = SandboxExecutor(
            Settings(max_output_bytes=5), client=self._client(container)
        )

        result = executor.execute("action-large", ["echo", "12345678"], {})

        self.assertEqual("12345", result["stdout"])
        self.assertEqual(8, result["stdout_bytes"])
        self.assertTrue(result["stdout_truncated"])
        self.assertEqual(64, len(result["stdout_sha256"]))

    def test_cleanup_only_targets_old_labeled_containers(self) -> None:
        old = Mock()
        old.attrs = {"Created": "2020-01-01T00:00:00Z"}
        recent = Mock()
        recent.attrs = {"Created": datetime.now(timezone.utc).isoformat()}
        client = Mock()
        client.containers.list.return_value = [old, recent]
        executor = SandboxExecutor(Settings(), client=client)

        self.assertEqual(1, executor.cleanup_stale_containers())
        old.remove.assert_called_once_with(force=True, v=True)
        recent.remove.assert_not_called()


if __name__ == "__main__":
    unittest.main()

