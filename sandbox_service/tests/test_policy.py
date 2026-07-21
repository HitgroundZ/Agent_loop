import unittest

from app.config import Settings
from app.policy import CommandPolicy, PolicyRejected


class CommandPolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = CommandPolicy(Settings())

    def test_allows_structured_python_and_safe_environment(self) -> None:
        decision = self.policy.validate(
            ["python", "-c", "print(6 * 7)"], {"LANG": "C.UTF-8"}
        )
        self.assertEqual("python-runtime", decision.rule)
        self.assertEqual("/workspace", decision.environment["HOME"])
        self.assertEqual("C.UTF-8", decision.environment["LANG"])

    def test_denies_dangerous_and_unknown_commands(self) -> None:
        for argv in (["rm", "-rf", "/"], ["bash", "-c", "id"], ["ruby", "-e", "puts 1"]):
            with self.subTest(argv=argv), self.assertRaises(PolicyRejected):
                self.policy.validate(list(argv), {})

    def test_denies_api_keys_and_host_paths(self) -> None:
        with self.assertRaises(PolicyRejected) as secret:
            self.policy.validate(["echo", "ok"], {"DASHSCOPE_API_KEY": "secret"})
        self.assertEqual("sensitive_env_denied", secret.exception.code)
        with self.assertRaises(PolicyRejected):
            self.policy.validate(["cat", "/etc/passwd"], {})

    def test_sleep_is_bounded_but_can_exercise_timeout(self) -> None:
        self.assertEqual("timeout-test", self.policy.validate(["sleep", "30"], {}).rule)
        with self.assertRaises(PolicyRejected):
            self.policy.validate(["sleep", "600"], {})


if __name__ == "__main__":
    unittest.main()

