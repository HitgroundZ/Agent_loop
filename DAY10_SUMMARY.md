# Day 10，2026-07-11：集成、加固、演示材料

## 最终结论

Agent Loop 已达到 Day 10 的面试演示级验收标准：完整系统可由 Docker Compose 一条命令构建并启动，浏览器可以完成知识库、Agent、审批、记忆、检索和 Trace 主流程；关键安全边界有确定性回归测试；架构、数据模型、状态机、5 分钟 Demo 和 2 分钟系统设计口述材料均已落库。

结论应准确表述为：**Demo-ready / portfolio-ready，尚非 production-ready。** 当前功能闭环完整，生产身份、可观测性、高可用和真实消息渠道仍是明确边界。

## 完整性审查

| 领域 | 状态 | 审查结论 |
|---|---|---|
| 文档入库 | 完成 | PDF/DOCX/MD/HTML、哈希去重、请求幂等、MinIO、版本、chunk 与删除清理均具备 |
| Embedding | 完成 | Redis 队列、Worker、批处理、最大重试、指数退避、失败状态和人工重置均具备 |
| 检索 | 完成 | keyword/vector/hybrid、RRF、真实 rerank、metadata 与 subject 权限过滤均具备 |
| Agent Loop | 完成 | 显式状态机、Function Calling、规则降级、token/限流、续跑与终态均具备 |
| 记忆 | 完成 | Redis 短期会话、PostgreSQL 长期记忆、来源、纠错、禁用和跨 session 召回均具备 |
| 权限与工具 | 完成 | 服务端 RBAC、意图授权、Tool Registry、风险分级、参数校验和敏感字段脱敏均具备 |
| 人工审批 | 完成 | pending 暂停、approve/reject、行锁、幂等、Outbox 和 Agent 自动续跑均具备 |
| 沙箱 | 完成 | 独立服务、结构化 argv、命令策略、无网络、非 root、只读、资源限制和销毁审计均具备 |
| Trace / Eval | 完成 | 每步 trace、引用 catalog 校验、handoff 评估、确定性回归与 live eval runner 均具备 |
| 前端 | 完成 | 六模块浏览器工作台；Day 10 使用生产构建和非 root 静态服务，并反向代理 API |
| 启动与验收 | 完成 | `docker compose up -d --build --wait`；一条脚本执行完整验收 |
| 演示材料 | 完成 | README、架构图、ER 图、状态机、时序图、5 分钟和 2 分钟讲稿均具备 |

## Day 10 主链路

1. 文档上传后计算 SHA-256，写入 MinIO，解析并生成 chunk。
2. Embedding job 进入 Redis；Worker 写回 pgvector，失败时按策略重试。
3. 检索在数据库层应用 tenant/workspace/subject 过滤，再执行 keyword/vector/hybrid 与 rerank。
4. Agent 根据原始用户意图和服务端权限选择可见工具。
5. 工具结果以本轮 `C<n>` / `M<n>` catalog 返回模型，知识内容不能授权副作用。
6. 高风险动作落为 pending ToolAction，前端审批后幂等续跑。
7. 命令进入独立 Docker 沙箱，策略拒绝或在受限一次性容器执行并销毁。
8. 最终答案校验 citation；缺少真实来源时转人工，全部状态写入 Trace 与 Eval。

## 关键测试补齐

Day 10 新增 `backend/tests/test_day10_hardening.py`，覆盖：

- 文档上传在同一幂等键下只入库和入队一次，并重放原始 201 响应。
- failed embedding job 可重置 attempts、error、next_run_at 和 chunk 状态，重复点击不重复入队。
- 私有 chunk 只对允许 subject 返回。
- 知识库片段中的删除指令不能扩大原始用户授权。
- 模型编造未知 citation 时，引用列表为空并强制 `escalated_to_human`。

与既有测试共同形成七项矩阵：文档幂等、embedding 重试、权限过滤、prompt injection、防幻觉、审批幂等、沙箱危险命令拒绝。

## 最终验证结果

`scripts/day10_verify.ps1` 已完整执行并以退出码 `0` 结束：

- Backend：32 cases；30 passed，2 个真实 DashScope 合约 case 按配置跳过。
- Sandbox unit：8 tests passed；4 个真实 Docker case 在单元阶段按设计跳过。
- Real Docker sandbox：4/4 passed，覆盖危险命令、网络/文件系统/凭据隔离、安全命令清理和超时销毁。
- Frontend：Vite production build passed。
- Live eval：5/5 passed，覆盖直接回答、记忆写入、跨 session 召回、危险沙箱命令和高风险审批暂停/续跑。
- Compose：Backend、Frontend、PostgreSQL、Redis、MinIO、Worker 与 Sandbox 均达到 healthy/running。

浏览器冒烟验证同样通过：总览显示服务在线；前端通过反向代理完成一次 Agent 问答并展示 `created → analyzing → evaluating → completed`；知识库页读取文档与 chunk；普通主体访问审批台返回权限不足，`demo-user` 可正常读取审批列表。

## 启动与完整验收

```powershell
docker compose up -d --build --wait
```

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\day10_verify.ps1
```

完整脚本会验证服务健康、后端测试、沙箱单元测试、真实容器隔离、前端生产构建和 5 个 live eval case。

## 演示与面试材料

- Demo 输入文档：`demo/day10-knowledge.md`
- 5 分钟逐段讲稿：`docs/DEMO_5_MIN.md`
- 2 分钟系统设计：`docs/SYSTEM_DESIGN_2_MIN.md`
- 架构 / ER / 状态机 / 时序：`docs/ARCHITECTURE.md`

## 生产化后续项

1. 接入 OIDC/企业 SSO，把用户、tenant 和角色从可信 token 注入。
2. 默认凭据迁移到 Secrets Manager，并固定/扫描基础镜像。
3. 为 Outbox 增加真实渠道 Worker、重试、死信和送达回执。
4. 引入 OpenTelemetry、Prometheus、集中日志、告警和 SLO。
5. 增加数据库备份恢复、Redis/MinIO HA、Worker 多副本与故障演练。
6. 扩展离线黄金数据集、LLM-as-judge 与人工标注闭环。
