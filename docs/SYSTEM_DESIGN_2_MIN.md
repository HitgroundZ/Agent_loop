# 2 分钟系统设计口述稿

“我做的是一个可审计的知识库 Agent，核心设计分四层。

第一层是知识库。文档上传后按 SHA-256 和幂等键去重，原文件和解析文本进 MinIO，metadata、chunk 和 1024 维向量进 PostgreSQL/pgvector。Embedding 通过 Redis + Worker 异步执行，有最大重试次数、指数退避和人工重置入口。检索支持 keyword、vector、hybrid RRF 和真实 rerank，并在 SQL 层按 tenant、workspace 和 subject 过滤权限。

第二层是 Agent Loop。每次运行是显式状态机：created、analyzing、acting、retrieving、waiting approval、evaluating，最终进入 completed、escalated to human 或 failed。短期会话在 Redis，原始消息、长期记忆、引用、token、运行结果和每一步 trace 都持久化到 PostgreSQL。知识库和记忆内容只作为不可信数据，不能转化为工具授权。

第三层是工具与安全。所有工具集中注册 permission、risk level、schema、timeout、retry 和敏感字段。角色映射只在服务端。高风险动作必须人工审批，并用行锁、唯一约束、Idempotency-Key 和 Outbox 避免重复执行。模型要执行命令时只能传结构化 argv 给独立 Sandbox Service；一次性容器无网络、只读、非 root、drop capabilities，并限制 CPU、内存、PID、输出和超时，结束后强制销毁。

第四层是可验证性。回答引用只能来自本轮检索 catalog；知识库路径没有真实 citation 时自动转人工。项目有确定性回归测试覆盖上传幂等、embedding 重试、权限过滤、prompt injection、防幻觉、审批幂等和危险命令拒绝；另外有 live eval 串联 API、工具、审批续跑和 trace。整个系统通过 Docker Compose 一条命令启动，前端运行已编译的生产静态资源而不是开发服务器。”

## 面试追问速答

**为什么不用向量库 SaaS？** MVP 用 PostgreSQL 同时承载事务数据、JSONB 过滤、FTS 和 pgvector，权限过滤与业务事务更容易保持一致；规模上升后可把向量检索抽成独立服务。

**如何防 prompt injection？** 来源内容永远不是授权源；可见工具由服务端角色、原始用户意图和风险策略三者交集决定，执行前再次校验目标和参数。

**如何保证审批不重复执行？** API 幂等记录返回第一次响应，ToolAction 行锁先抢占 `running`，`(run_id, tool_call_id)` 唯一约束防重复落库，外部投递再以 action ID 作为幂等键。

**系统目前最大边界是什么？** 这是单机 Docker Compose 的面试级完整系统；真实生产还需要接企业 IdP、Secrets Manager、集中监控告警、备份恢复和多副本任务协调。
