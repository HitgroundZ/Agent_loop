# Day 10 Live Evals

`run_evals.py` 会通过真实 HTTP API 跑一组小型端到端评测，检查 Agent 的终态、工具选择、审批暂停/续跑、引用和 trace。它不替代 `backend/tests` 的确定性回归测试，而是验证已启动系统的串联效果。

运行：

```powershell
docker compose run --rm evals
```

默认生成 `day10-eval-*` 专用主体，默认服务端角色映射只为这个受限前缀授予 operator 与 approver。每轮和每类 case 使用独立主体，既与日常演示用户隔离，也不会继承历史 token budget。高风险消息用例会在验证待审批后自动执行“拒绝”，避免留下未处理 action。

可直接在宿主机运行：

```powershell
python .\evals\run_evals.py --base-url http://localhost:8000
```

退出码为 `0` 表示全部通过，`1` 表示至少一个 case 未满足断言。用例定义见 `cases.json`。

说明：真实模型不可用时，Agent 会进入规则降级路径；这些 case 仍然可运行。知识库答案质量和 rerank 效果应在 5 分钟人工 demo 中结合真实文档检查。

## Ragas 黄金集评测

`datasets/agent-loop-v1` 是基于 `demo/day10-knowledge.md` 的 10 条中文 smoke 黄金集。先将该文档上传并完成切片，随后可在前端“评测中心”发起异步评测。

它与上述 live eval 的用途不同：live eval 验证流程断言，黄金集评测量化 Hit@K、faithfulness、answer relevancy、context precision 和 context recall。完整字段、评分口径和扩充方式见 `docs/EVALUATION.md`。
