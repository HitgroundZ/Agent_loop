from __future__ import annotations

import os
import unittest
from uuid import uuid4

from app.config import Settings
from app.executor import SandboxExecutor
from app.policy import PolicyRejected


@unittest.skipUnless(
    os.getenv("RUN_DOCKER_SANDBOX_INTEGRATION") == "1",
    "设置 RUN_DOCKER_SANDBOX_INTEGRATION=1 后运行真实 Docker 沙箱验收",
)
class DockerSandboxIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(execution_timeout_seconds=1)
        self.executor = SandboxExecutor(self.settings)
        self.executor.health()

    def test_safe_command_and_cleanup(self) -> None:
        execution_id = f"integration-safe-{uuid4()}"
        result = self.executor.execute(
            execution_id, ["python", "-c", "print(6*7)"], {}
        )
        self.assertEqual("succeeded", result["execution_status"])
        self.assertEqual("42", result["stdout"].strip())
        self.assertTrue(result["cleanup"]["container_removed"])
        self.assertEqual([], self._containers(execution_id))

    def test_dangerous_command_is_rejected_before_create(self) -> None:
        execution_id = f"integration-denied-{uuid4()}"
        with self.assertRaises(PolicyRejected):
            self.executor.execute(execution_id, ["rm", "-rf", "/"], {})
        self.assertEqual([], self._containers(execution_id))

    def test_timeout_is_killed_and_removed(self) -> None:
        execution_id = f"integration-timeout-{uuid4()}"
        result = self.executor.execute(execution_id, ["sleep", "30"], {})
        self.assertEqual("timed_out", result["execution_status"])
        self.assertTrue(result["timed_out"])
        self.assertEqual([], self._containers(execution_id))

    def test_network_root_filesystem_and_secret_environment_are_isolated(self) -> None:
        network_code = (
            "import socket\n"
            "try:\n socket.create_connection(('1.1.1.1', 53), .2); print('connected')\n"
            "except Exception: print('blocked')"
        )
        network = self.executor.execute(
            f"integration-network-{uuid4()}", ["python", "-c", network_code], {}
        )
        self.assertEqual("blocked", network["stdout"].strip())

        fs_code = (
            "import os\n"
            "root='writable'\n"
            "try:\n open('/root-proof','w').write('x')\n"
            "except Exception: root='readonly'\n"
            "open('/workspace/proof','w').write('ok')\n"
            "print(root, open('/workspace/proof').read(), "
            "'DASHSCOPE_API_KEY' in os.environ, 'SANDBOX_SERVICE_TOKEN' in os.environ)"
        )
        filesystem = self.executor.execute(
            f"integration-filesystem-{uuid4()}", ["python", "-c", fs_code], {}
        )
        self.assertEqual("readonly ok False False", filesystem["stdout"].strip())

    def _containers(self, execution_id: str):
        return self.executor.client.containers.list(
            all=True,
            filters={"label": f"com.agent-loop.execution-id={execution_id}"},
        )


if __name__ == "__main__":
    unittest.main()
