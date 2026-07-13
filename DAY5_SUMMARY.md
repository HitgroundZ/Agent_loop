# Day 5 长短期记忆实现总结

## 完成范围

Day 5 在 Day 4 智能体状态机与 trace 的基础上，补齐了用户级长短期记忆，并将记忆召回加入智能体执行流：

```text
created -> analyzing -> recalling -> retrieving -> acting
        -> waiting_approval -> evaluating -> completed / escalated_to_human
```

核心原则是“先按 `user_id` 检索少量相关、启用的记忆，再把这部分内容加入本轮 prompt context”，不会把某个用户的全部历史记录塞入上下文。每次 `run` 都会返回 `memory_context`，`recalling` trace 会记录候选数、注入数、字符数、缓存命中和来源 ID，便于审计。

## Redis 短期记忆

会话快照 key：

```text
agent_loop:sessions:{session_id}
```

会话快照现在包含：

- `recent_messages`：最近 N 条消息，同时保留 PostgreSQL `conversation_messages.id`。
- `pending_approval`：等待审批的 run、状态和创建时间。
- `retrieval_cache`：本次长期记忆检索的 query hash、是否命中、结果数。
- `rate_limit`：用户固定窗口请求数、上限和剩余额度。
- `token_budget`：用户当日已用、上限和剩余 token。
- Day 4 已有的 `task_status`、`temporary_plan`、`retrieval_intermediate`、`trace_preview`。

独立 Redis key：

```text
agent_loop:rate_limit:{user_id}:{window_id}
agent_loop:token_budget:{user_id}:{yyyy-mm-dd}
agent_loop:memory_version:{user_id}
agent_loop:memory_cache:{user_id}:{version}:{query_sha256}
```

限流和 token budget 在 Redis 可用时生效；Redis 故障时请求会 fail-open，但 session 返回的 `redis.available/error` 会明确暴露降级状态，避免缓存故障导致核心问答完全不可用。

## PostgreSQL 长期记忆

Alembic revision：`202607060001`。

### `conversation_messages`

保存原始对话，不用 Redis TTL 代替长期存档：

- `id`
- `user_id`
- `session_id`
- `run_id`
- `role`
- `content`
- `created_at`

### `long_term_memories`

统一承载四类可检索记忆：

- `event_summary`：一次 run 的问题与处理结果摘要。
- `scene`：用户在特定会话中提到或询问的场景信息。
- `user_profile`：用户明确表达的姓名、偏好、习惯、职业等信息。
- `human_correction`：人工纠错后的新事实。

关键字段：

- `source_message_id` / `source_document_id`：至少一个来源标识，支持回到原始消息或文档。
- `parent_memory_id`：人工纠错指向被纠正的记忆。
- `enabled`：禁用后立即从召回候选中排除。
- `metadata`、`access_count`、`last_accessed_at`：保存生成上下文和召回统计。

来源 ID 刻意作为稳定标识保存，而不是在源文档删除时自动置空；因此即使外部源后来被清理，记忆记录仍保留原始追溯 ID。

## 选择性召回与注入

1. 只读取当前 `user_id` 且 `enabled=true` 的候选记忆。
2. 从最近的有限候选集中计算中英文关键词、汉字及双字词重合度。
3. 人工纠错和用户画像有更高优先级。
4. 去重后受 `MEMORY_RETRIEVAL_LIMIT` 与 `MEMORY_CONTEXT_MAX_CHARS` 双重限制。
5. 仅把最终选中的 `memory_context` 交给回答步骤。
6. 文档引用使用 `[C<n>]`，长期记忆引用使用 `[M<n>]`，后者带 `memory_id`、`source_message_id`、`source_document_id`。

当知识库没有文档命中但存在相关长期记忆时，智能体可以使用可追溯记忆完成回答；既没有文档引用也没有相关记忆时仍会转人工。

## 记忆管理 API

- `GET /api/memories?user_id=...`：查看长期记忆，可按类型、启用状态过滤。
- `GET /api/memories/messages?user_id=...`：查看 PostgreSQL 原始对话。
- `POST /api/memories`：创建带来源 ID 的人工记忆。
- `PATCH /api/memories/{memory_id}`：启用或禁用。
- `POST /api/memories/{memory_id}/corrections`：禁用原记忆并创建可追溯的人工纠错记忆。
- `DELETE /api/memories/{memory_id}`：删除长期记忆，不删除原始对话。

`POST /api/agent/runs` 新增 `user_id`；跨会话召回依赖 `user_id`，`session_id` 只代表短期会话边界。

## 前端工程化拆分

原先集中在 `App.vue` 的页面布局和方法已经拆开：

```text
frontend/src/App.vue                              应用壳
frontend/src/views/WorkspaceView.vue              工作区页面布局
frontend/src/composables/useKnowledgeWorkspace.js 文档、检索、Agent 状态与方法
frontend/src/services/api.js                      HTTP 请求与错误处理
frontend/src/components/MemoryManager.vue         记忆查看、禁用、删除、纠错、原文追溯
```

Agent 面板新增用户 ID、`recalling` 状态、选择性注入列表和 Redis 短期记忆状态；记忆管理面板支持按类型/状态筛选、查看原始消息、启停、删除和内联纠错。

## 配置项

```text
AGENT_RATE_LIMIT_REQUESTS=60
AGENT_RATE_LIMIT_WINDOW_SECONDS=60
AGENT_TOKEN_BUDGET=12000
MEMORY_RETRIEVAL_LIMIT=5
MEMORY_CANDIDATE_LIMIT=200
MEMORY_CONTEXT_MAX_CHARS=2400
MEMORY_CACHE_TTL_SECONDS=300
```

## 当天验收结果

已完成以下实际验收：

- Python `compileall` 通过。
- 前端 `npm run build` 通过。
- Alembic 成功升级 `202607050001 -> 202607060001`。
- 同一 `user_id` 从 `session-a` 切换到 `session-b` 后召回 2 条相关记忆，并带有效 `source_message_id`。
- 无文档命中时，第二个会话仍基于长期记忆进入 `completed`。
- 人工纠错后，后续会话只注入纠正后的内容。
- 全部禁用后，后续 run 的 `memory_context` 为 0。
- 删除接口返回成功，记忆总数相应减少。
- 浏览器页面验收通过：能加载记忆管理列表、显示 `recalling` 状态、短期额度与本轮注入记忆；实际页面 run 为 `completed`。
