# Day 6 工具调用、风险策略与人工审批总结

## 统一工具内核

Day 6 已与 Day 5 路由修订合并，不再保留临时 Function Calling 实现。`ToolRegistry` 定义工具 schema、权限、风险、副作用、超时、重试和敏感字段；`ToolExecutor` 负责校验、审计与执行。

已注册工具：

| 工具 | 权限 | 风险 | 执行策略 |
|---|---|---:|---|
| `search_user_memory` | `memory.read` | 低 | 自动执行 |
| `search_knowledge_base` | `knowledge.read` | 低 | 自动执行 |
| `save_long_term_memory` | `memory.write` | 中 | 明确记忆请求且 `auto_approve=true` 时执行，否则审批 |
| `delete_document` | `document.delete` | 高 | 始终审批 |
| `enqueue_message` | `message.send` | 高 | 始终审批，批准后只写 Outbox |
| `call_webhook` | `external.call` | 高 | 始终审批，HTTPS 白名单与私网阻断 |

角色由服务端 `TOOL_ROLE_ASSIGNMENTS` 映射。模型和请求体不能声明自身权限。默认 `user` 可读知识库和读写记忆；`operator` 可提出副作用 action；`approver` 可查看和处理审批。

## Agent 路由与检索

`POST /api/agent/runs` 支持：

- `retrieval_mode=auto`：LLM 判断是否调用知识库。
- `retrieval_mode=always`：强制调用知识库。
- `retrieval_mode=never`：不向模型暴露知识库工具。
- `strategy` 仅决定 vector、keyword 或 hybrid 检索算法。

最多执行 3 轮工具调用。工具参数中的 user、workspace、权限和过滤器由服务端绑定。没有知识库调用时 `retrieval_result.status=skipped`，不会产生伪造的 `retrieving` trace。

知识库先召回 `max(top_k × 4, 20)` 个候选，再调用 `qwen3-rerank`，默认最低分 `0.35`。rerank 未配置、失败或超时会 fail-closed，不把未经验证的片段交给回答模型。最终只保留回答实际使用且属于本轮 catalog 的 `[C<n>]` / `[M<n>]`。

## 不可信上下文边界

服务在检索前从原始用户消息生成不可扩张的 `IntentAuthorization`。文档、长期记忆和工具结果均作为不可信上下文，不能扩大副作用授权。

- 原始消息未要求删除、发送或 Webhook 时，相关 action 直接 `blocked`。
- 副作用目标必须能回溯到原始消息或可信服务端实体解析。
- 文档片段中的文档 ID、收件人或 URL 不能直接成为副作用目标。

## 审批、幂等与续跑

`tool_actions` 保存工具调用、脱敏参数、权限、风险、授权证据、审批、执行结果、错误和完整时间字段，并以 `(run_id, tool_call_id)` 唯一。`tool_outbox.action_id` 唯一，保证发送 action 只入队一次。

审批 API：

- `GET /api/tool-actions`
- `GET /api/tool-actions/{action_id}`（包含关联 trace）
- `POST /api/tool-actions/{action_id}/approve`
- `POST /api/tool-actions/{action_id}/reject`

决策请求必须带 `Idempotency-Key` 和 `X-Principal-Id`。审批使用数据库行锁，并在锁内原子占有 `running` 状态；重复请求返回首次响应，冲突决策返回 409。全部 pending action 处理完后，工具结果会作为 tool message 自动恢复模型循环。

## Webhook 安全

Webhook 仅允许 `TOOL_WEBHOOK_ALLOWED_HOSTS` 中的 HTTPS 主机，禁止重定向、私网、回环、链路本地、保留和组播地址。响应体有大小限制，默认超时 10 秒，并携带 action ID 作为外部幂等键。不确定超时不会自动重试。

## 前端审批台

前端新增独立“审批台”模块：

- 左侧和移动导航显示 pending 数量。
- 可按状态查看 action、脱敏参数、风险、权限、授权证据和关联 trace。
- 可填写理由后批准或拒绝。
- 单次请求结束前固定使用同一个幂等键。
- 展示 `executed/rejected/failed/blocked` 等终态。

Agent 页面同步展示 retrieval mode、路由来源、知识库决策、工具结果和 `waiting_approval` 提示。

## 验收

自动化测试覆盖：原子记忆、跨会话召回、隐式记忆审批、角色权限、提示注入阻断、文档记忆 citation、rerank 阈值/fail-closed、高风险发送审批、重复批准、Outbox 唯一，以及 Webhook 白名单/私网阻断/超时/脱敏。当前共运行 21 项，其中 19 项通过、2 项可选真实 DashScope 测试默认跳过；前端 production build 通过。
