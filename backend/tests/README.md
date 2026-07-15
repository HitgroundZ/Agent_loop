# Day 5 / Day 6 数据库与 Agent 测试

测试连接真实 PostgreSQL 和 Redis，不用 SQLite 替代 JSONB、行锁、唯一约束与跨会话缓存行为。外部模型、rerank 和 Webhook 在默认测试中使用 fake/mock。

## 自动化案例

`test_day5_memory.py`：

- 核对 revision `202607070001`、长期记忆字段、索引与来源约束。
- 验证原子画像写入、跨 session 召回、人工纠错、禁用与删除。
- 验证来源消息保留，普通会话记忆的 `source_document_id` 为空。

`test_day6_tools.py`：

- 核对 `agent_runs` 路由字段、`tool_actions`、`tool_outbox` 和唯一约束。
- 显式记忆不调用知识库，并拆为两条 `user_profile`。
- 记忆跨会话仅调用 `search_user_memory` 并使用 `[M1]`。
- 隐式记忆进入审批且审批前不写入。
- 高风险发送审批前无副作用，批准后自动续跑；重复幂等请求只产生一条 Outbox。
- 无权限工具不暴露，文档上下文不能扩张副作用授权。
- `qwen3-rerank` 阈值过滤与未配置时 fail-closed。
- Webhook 白名单、私网阻断、禁止重定向、超时、响应截断和敏感结果脱敏。

`test_dashscope_optional.py`：

- 默认跳过。
- 显式设置 `RUN_DASHSCOPE_INTEGRATION=1` 与 `DASHSCOPE_API_KEY` 后验证真实 `qwen3.7-max` 和 `qwen3-rerank` 连通性。

运行：

```powershell
docker-compose build backend
docker-compose run --rm backend python -m unittest discover -s tests -v
```

## 生成可查看的长期记忆 demo

```powershell
docker exec agent-loop-backend-1 python -m tests.seed_day5_memory_demo
```

脚本只重建 `day5-structure-demo`，生成两个 session、原始 user/assistant 消息、两条原子用户画像和一条人工纠错。会话事实仅写 `source_message_id`，不会为了演示而错误关联任意文档。

## 检查表结构和数据

结构化输出（包含 `tool_actions` 与 `tool_outbox`）：

```powershell
docker exec agent-loop-backend-1 python -m tests.inspect_day5_memory
```

直接使用 psql：

```powershell
Get-Content -Raw backend/tests/sql/inspect_day5_memory.sql | docker exec -i agent-loop-postgres-1 psql -U agent_loop -d agent_loop
```

前端切换到 `day5-structure-demo` 可查看记忆；切换到服务端配置为 approver 的用户（默认 `demo-user`）可进入审批台查看 action。
