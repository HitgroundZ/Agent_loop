# Day 5 长期记忆测试

这组测试针对真实 PostgreSQL/Redis，不使用 SQLite 替代 PostgreSQL JSONB、索引和外键行为。

## 测试案例

`test_day5_memory.py` 包含两个可重复执行并自动清理的测试：

1. `test_schema_contract`
   - 断言 Alembic revision 为 `202607060001`。
   - 断言 `agent_runs`、`conversation_messages`、`long_term_memories` 存在。
   - 核对长期记忆字段、非空约束、索引和原始消息外键。
   - 核对 `agent_runs.user_id` 与 `agent_runs.memory_context`。
2. `test_memory_lifecycle_and_cross_session_recall`
   - 在 session A 写入原始用户消息。
   - 自动生成事件摘要、场景记忆和用户画像。
   - 在 session B 召回并核对 `source_message_id` 与原文。
   - 创建人工纠错，核对 `parent_memory_id`。
   - 禁用全部记忆后验证召回结果为 0。
   - 删除纠错记忆，并确认原始对话仍存在。

运行测试：

```powershell
docker-compose up -d --build backend
docker exec agent-loop-backend-1 python -m unittest discover -s tests -p "test_*.py" -v
```

## 生成可保留的 demo 数据

自动化测试会清理自身数据。如果希望在数据库中保留一组方便人工查看的记录，运行：

```powershell
docker exec agent-loop-backend-1 python -m tests.seed_day5_memory_demo
```

脚本只重建固定用户 `day5-structure-demo`，会生成：

- 两个不同 session 的 `agent_runs`。
- 用户和助手原始对话。
- `event_summary`、`scene`、`user_profile`、`human_correction` 四类长期记忆。
- 已禁用的原画像、启用的人工核验记录及 `parent_memory_id`；核验只修正文案，不凭空改变用户事实。
- `source_message_id`，数据库已有文档时也会写入一个 `source_document_id` 示例。

## 查看表结构和 demo 数据

结构化 Python 输出：

```powershell
docker exec agent-loop-backend-1 python -m tests.inspect_day5_memory
```

直接使用 psql：

```powershell
Get-Content -Raw backend/tests/sql/inspect_day5_memory.sql | docker exec -i agent-loop-postgres-1 psql -U agent_loop -d agent_loop
```

前端记忆管理页面也可直接把用户 ID 切换为 `day5-structure-demo` 查看这些记录。
