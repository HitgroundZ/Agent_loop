# Day 5 长短期记忆实现总结（Day 6 合并修订版）

## 交付结果

Day 5 已完成 Redis 短期状态、PostgreSQL 长期记忆、跨会话召回、来源追溯和前端记忆管理。根据验收中发现的错误知识库关联问题，本次又将记忆写入统一纳入工具路由：只有 `save_long_term_memory` 可以创建自动长期记忆，不再为每轮对话固定生成 `event_summary + scene`，也不再从 citations 第一项猜测 `source_document_id`。

当前执行链为：

```text
保存原始消息 → 建立意图授权 → LLM 选择工具 → 风险/权限/来源校验
→ 自动执行或等待审批 → LLM 最终回答 → 引用校验 → 完成
```

## Redis 短期记忆

会话快照 `agent_loop:sessions:{session_id}` 保存：

- 最近消息及其 PostgreSQL `conversation_messages.id`。
- `pending_approval`、临时计划、任务状态和 trace preview。
- 记忆检索缓存状态、rate limit 和 token budget。

独立 key 包括：

```text
agent_loop:rate_limit:{user_id}:{window_id}
agent_loop:token_budget:{user_id}:{yyyy-mm-dd}
agent_loop:memory_version:{user_id}
agent_loop:memory_cache:{user_id}:{version}:{query_sha256}
```

Redis 故障时核心数据库流程可降级运行，返回状态中会暴露 Redis 不可用信息。

## PostgreSQL 长期记忆

Day 5 表为 `conversation_messages` 与 `long_term_memories`；当前总迁移版本为 `202607070001`。

`conversation_messages` 始终保存 user/assistant 原始消息。`long_term_memories` 支持：

- `event_summary`、`scene`、`user_profile`、`human_correction` 类型。
- `enabled` 禁用、删除、人工纠错及 `parent_memory_id`。
- `source_message_id` / `source_document_id` 来源追溯。
- 按用户、类别和规范化内容去重。

自动写入规则已经收紧：

1. 会话事实只写当前原始 `source_message_id`，`source_document_id=NULL`。
2. 文档衍生记忆必须显式提供一个本轮有效 `[C<n>]`；引用无效或缺少 document ID 时拒绝保存。
3. `human_correction` 只能由人工纠错 API 创建。
4. 复合信息拆为原子事实。例如“请记住我是一名 AI 工程师，并且偏好简洁回答”写入两条 `user_profile`。
5. 隐式推断出的长期记忆是中风险 action，必须审批后才写入。

## 选择性召回

模型可调用 `search_user_memory`。服务只读取当前 `user_id`、`enabled=true` 且与问题相关的有限候选，并受以下配置约束：

```text
MEMORY_RETRIEVAL_LIMIT=5
MEMORY_CANDIDATE_LIMIT=200
MEMORY_CONTEXT_MAX_CHARS=2400
MEMORY_CACHE_TTL_SECONDS=300
```

被选中的记忆以 `[M<n>]` 进入本轮 prompt 和返回引用；没有调用记忆工具时不会全量注入历史。

## 记忆管理 API 与前端

- `GET /api/memories?user_id=...`
- `GET /api/memories/messages?user_id=...`
- `PATCH /api/memories/{memory_id}`
- `POST /api/memories/{memory_id}/corrections`
- `DELETE /api/memories/{memory_id}`

前端已拆为 App shell、模块化 views、共享 composable 和 HTTP service。长期记忆模块支持查看、类型/状态过滤、原文追溯、禁用、删除和纠错；Agent 模块展示本轮选择性注入的记忆与 `[M<n>]` 来源。

## 验收结果

- 显式“请记住”不会调用知识库，生成两条原子画像，来源消息一致且文档来源为空。
- 同一用户跨 session 只调用记忆工具，可使用 `[M1]` 回答。
- 隐式“我最近在做 Agent 项目”进入 `waiting_approval`，审批前数据库没有新增长期记忆。
- 禁用或删除后，后续检索不会再注入该记忆。
- PostgreSQL schema、来源字段、索引与生命周期测试通过。

Day 6 的 Tool Registry、风险审批和续跑实现见 [DAY6_SUMMARY.md](./DAY6_SUMMARY.md)。
