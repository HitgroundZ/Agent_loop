# RAG 量化评测系统

## 指标口径

评测 worker 通过真实 `POST /api/agent/runs` 链路执行黄金集，固定 `retrieval_mode=always`、显式 Top K、启用现有 rerank，并为每个案例创建独立 user/session。

| 指标 | 口径 |
|---|---|
| Hit@K | Top K 检索结果至少包含一个黄金 `context_id` 的案例比例 |
| Faithfulness | Ragas 判断实际回答中的事实能否由实际检索上下文支持 |
| Answer relevancy | Ragas 判断实际回答与问题意图的相关程度 |
| Context precision | Ragas 按排序判断检索上下文对参考答案是否有用 |
| Context recall | Ragas 判断检索上下文覆盖了参考答案中的多少事实 |

所有分数在 API 中使用 `0..1`，前端转换为百分比并保留一位小数。Pipeline 没有产生回答或上下文时按端到端失败计 0；judge 超时、格式错误等基础设施问题记为 `null`，聚合时不伪装成 0，同时通过 `coverage` 展示已评分数量。

## 数据与持久化

黄金集格式见 `evals/datasets/README.md`。`evaluation_runs` 保存数据集版本、检索配置、judge/embedding 模型、进度、聚合指标和覆盖率；`evaluation_case_results` 保存 Agent run、问题、参考/实际回答、参考/实际上下文、逐指标分数、reason、Hit@K 和错误。

批次状态为：

- `queued`：已持久化并等待 Redis worker。
- `running`：正在执行 Agent 与 Ragas。
- `completed`：全部案例与指标成功。
- `completed_with_errors`：存在 pipeline 或 judge 局部失败，仍保留有效分数。
- `failed`：数据集变化或 worker 发生批次级错误。

## 运行

1. 在 `.env` 配置 `DASHSCOPE_API_KEY`，可按需覆盖 `EVAL_LLM_MODEL`、`EVAL_EMBEDDING_MODEL`、并发、超时和重试。
2. 启动完整栈：`docker compose up -d --build --wait`。
3. 上传 `demo/day10-knowledge.md`；数据集校验通过后进入前端“评测中心”。
4. 选择数据集、检索策略和 Top K，点击“开始评测”。页面每 2 秒轮询进度。

发起 API 必须携带 `Idempotency-Key`：

```http
POST /api/evaluations/runs
Idempotency-Key: <uuid>
Content-Type: application/json

{"dataset_id":"agent-loop-v1","strategy":"hybrid","top_k":5}
```

默认 CI 使用 fake judge，不消费真实模型额度：

```powershell
docker compose run --rm backend python -m unittest discover -s tests -v
docker compose run --rm evaluation-worker python -m unittest -v test_worker
docker compose run --rm frontend-check
```

Ragas 固定在 `0.4.x`，LangChain 固定在兼容的 `0.3.x`；升级前必须重新运行 scorer 初始化和 worker 单测。
