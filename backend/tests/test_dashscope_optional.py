"""可选真实 DashScope 验证；默认跳过，避免 CI 产生网络请求和费用。"""

from __future__ import annotations

import os
import unittest

from app.config import Settings
from app.services.agent_model import AgentModelClient
from app.services.retrieval import DashScopeReranker


@unittest.skipUnless(
    os.getenv("RUN_DASHSCOPE_INTEGRATION") == "1" and os.getenv("DASHSCOPE_API_KEY"),
    "设置 RUN_DASHSCOPE_INTEGRATION=1 和 DASHSCOPE_API_KEY 后运行",
)
class DashScopeIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings()

    def test_qwen_agent_model(self) -> None:
        turn = AgentModelClient(self.settings).complete([
            {"role": "system", "content": "只回复 OK。"},
            {"role": "user", "content": "连通性测试"},
        ])
        self.assertTrue(turn.content.strip())

    def test_qwen_reranker(self) -> None:
        ranked, diagnostics = DashScopeReranker(self.settings).rerank(
            "用户从事什么职业？",
            [
                {"id": "tea", "snippet": "用户喜欢茉莉花茶。", "score": 0.6},
                {"id": "job", "snippet": "用户是一名 AI 工程师。", "score": 0.5},
            ],
            enabled=True,
            top_k=2,
        )
        self.assertTrue(diagnostics["rerank_applied"])
        self.assertTrue(ranked)
        self.assertEqual("job", ranked[0]["id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
