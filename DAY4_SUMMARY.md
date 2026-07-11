# Day 4 Agent Loop 状态机与 Trace 说明

## 完成范围

本次实现了同步版 Agent run，用一次 `POST /api/agent/runs` 请求跑完整问答链路，并将状态、trace、Redis 会话缓存和前端展示串起来。

状态流：

```text
created -> analyzing -> retrieving -> acting -> waiting_approval -> evaluating -> completed
```

当检索不到可靠引用时，终态为：

```text
created -> analyzing -> retrieving -> acting -> waiting_approval -> evaluating -> escalated_to_human
```

执行异常时会写入 `failed` 终态。

## 后端实现

新增数据表：

- `agent_runs`：保存一次 Agent run 的 session、问题、当前状态、答案、引用、临时 plan、检索结果、评估结果、token 统计、重试次数和错误。
- `agent_trace_events`：保存每个状态事件，包含 `input_payload`、`output_summary`、`output_payload`、`duration_ms`、`token_usage`、`error`、`retry_count`。

新增 API：

- `POST /api/agent/runs`：发起一次 Agent 问答。
- `GET /api/agent/runs/{run_id}`：查看一次 run 的完整结果和 trace。
- `GET /api/agent/sessions/{session_id}`：查看 Redis 中保存的会话快照。

检索策略：

- 默认支持 `hybrid`、`vector`、`keyword`。
- 当 `hybrid` 或 `vector` 因向量配置不可用失败时，会 retry 到 `keyword`，并在 retrieving trace 中记录 fallback 错误和 retry 次数。

回答策略：

- Day4 采用抽取式 grounded answer：基于检索到的 chunk snippet 生成答案，并返回 citations。
- 如果没有 citation，则进入 `escalated_to_human`，避免输出无依据答案。

## Redis 会话缓存

Redis key：

```text
agent_loop:sessions:{session_id}
```

保存内容：

- `recent_messages`：最近 N 条用户/助手消息。
- `task_status`：当前 run id、状态、是否终态、错误、重试次数。
- `temporary_plan`：本轮临时计划。
- `retrieval_intermediate`：检索 query、策略、top_k、候选数量、前 5 条候选摘要、diagnostics。
- `trace_preview`：简版 trace，便于聊天页快速展示。

默认保留最近 12 条消息，TTL 为 24 小时，可通过配置项调整：

- `AGENT_SESSION_MESSAGE_LIMIT`
- `AGENT_SESSION_TTL_SECONDS`

## 前端实现

聊天页新增 `Day 4 / Agent Run` 面板：

- 输入问题并发起 Agent run。
- 复用现有检索区域的 strategy、top_k、tenant、workspace、tags、principal 和当前文档过滤条件。
- 展示当前状态、状态流、答案、引用、trace 简版。
- 支持展开完整 `trace_events` JSON。
- 展示 Redis 最近会话消息。

## 验收结果

已完成以下验证：

- `python -m compileall backend\app` 通过。
- `npm run build` 通过。
- Alembic 已升级到 `202607050001`。
- 数据库已创建 `agent_runs` 和 `agent_trace_events`。
- `POST /api/agent/runs` keyword 烟测通过：
  - 命中问题 `生态环境监测`
  - 状态流完整到 `completed`
  - 返回 3 条 citations
  - 写入 7 条 trace events
  - Redis session 可读取
- 浏览器验收通过：
  - 前端能提交 Agent run
  - 页面能看到 `completed`
  - 页面能看到 Answer、Citations、Trace、created、retrieving、evaluating 等状态信息

