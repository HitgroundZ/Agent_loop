# Agent Loop Knowledge Base

Agent Loop 是一个可运行、可审批、可追踪的企业知识库 Agent MVP。Day 10 版本已经串联完整主流程：

> 文档上传 → 解析与切片 → embedding / 检索 → Agent 回答 → 高风险人工审批 → Trace → Eval

前端提供总览、智能体、审批台、长期记忆、知识库、检索实验室和评测中心七个模块。后端使用 FastAPI、PostgreSQL/pgvector、Redis、MinIO、Qwen/DashScope 和独立 Docker Sandbox Service。

## 快速启动

首次运行：

```powershell
Copy-Item .env.example .env
```

如需真实 LLM、embedding 和 rerank，在 `.env` 中配置：

```text
DASHSCOPE_API_KEY=<your-key>
```

一条命令启动全部服务并等待健康检查通过：

```powershell
docker compose up -d --build --wait
```

访问：

- 前端：<http://localhost:5173>
- Backend health：<http://localhost:8000/api/health>
- OpenAPI：<http://localhost:8000/docs>
- MinIO Console：<http://localhost:9001>

默认演示用户为 `demo-user`，服务端为其映射 `operator` 和 `approver`。真实部署必须替换默认数据库、MinIO 和 Sandbox token，并接入正式身份系统。

## 核心能力

### 知识库

- 支持 PDF、DOCX、Markdown 和 HTML。
- SHA-256 内容去重，`Idempotency-Key` 请求重放。
- 原文件与解析文本存 MinIO；chunk、metadata、权限和向量存 PostgreSQL。
- Redis + Worker 异步 embedding，最多 3 次重试，指数退避，可人工重置失败任务。
- keyword、vector、hybrid RRF 和 Qwen rerank。
- tenant、workspace、document、tag、时间和 subject 权限在 SQL 检索阶段过滤。

### Agent 与记忆

- LLM Function Calling + 确定性规则降级。
- Redis 短期会话、限流和 token budget。
- PostgreSQL 长期记忆，支持来源追踪、禁用、纠错和跨 session 召回。
- 每次运行保存 plan、routing decision、tool action、citation、evaluation 和完整 trace。
- 知识库路径没有本轮真实引用时，终态自动转为 `escalated_to_human`。

### 工具、审批与沙箱

- 统一 Tool Registry 定义 schema、permission、risk、timeout、retry 和敏感字段。
- 角色只从服务端配置读取，模型或前端不能自行提升权限。
- 文档、记忆和工具结果均为不可信数据，不能扩大原始用户意图授权。
- 高风险操作必须人工审批；行锁、唯一约束、幂等记录和 Outbox 避免重复执行。
- 命令执行只接受结构化 `argv`，由独立 Sandbox Service 创建一次性容器。
- 沙箱默认无网络、只读根文件系统、非 root、drop all capabilities，并限制 CPU、内存、PID、输出和超时；结束后删除容器。

### RAG 量化评测

- Ragas 0.4 计算 faithfulness、answer relevancy、context precision 和 context recall。
- 黄金上下文使用规范化切片内容的 SHA-256 `context_id`，另行计算确定性的 Hit@K。
- 独立 evaluation worker 从 Redis 异步消费批次；每个案例使用隔离的 Agent 用户和 session。
- PostgreSQL 持久化数据集版本、judge 配置、聚合分数、覆盖率、逐案例证据与评分理由。
- 前端评测中心提供任务进度、五项 KPI、历史趋势、策略对比和低分案例诊断。

## 常用 API

| 领域 | API |
|---|---|
| 文档 | `POST /api/documents/upload` |
| 文档 | `GET /api/documents` |
| 文档 | `GET /api/documents/{id}/chunks` |
| Embedding | `POST /api/documents/{id}/embedding-jobs` |
| 检索 | `POST /api/retrieval/search` |
| 检索 | `POST /api/retrieval/compare` |
| Agent | `POST /api/agent/runs` |
| Agent | `GET /api/agent/runs/{id}` |
| Trace/审批 | `GET /api/tool-actions/{id}` |
| 审批 | `POST /api/tool-actions/{id}/approve` |
| 审批 | `POST /api/tool-actions/{id}/reject` |
| 记忆 | `GET /api/memories?user_id=...` |
| 评测数据集 | `GET /api/evaluations/datasets` |
| 发起评测 | `POST /api/evaluations/runs` |
| 评测历史 | `GET /api/evaluations/runs` |
| 评测详情 | `GET /api/evaluations/runs/{id}` |

上传、删除和审批建议始终携带 `Idempotency-Key`。审批接口还必须提供服务端已映射为 approver 的 `X-Principal-Id`。

## 验收

完整 Day 10 验收：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\day10_verify.ps1
```

脚本依次执行：构建并健康启动、后端全量测试、沙箱单元测试、evaluation worker 单元测试、真实 Docker 隔离测试、前端生产构建和 live eval。

单独运行：

```powershell
docker compose run --rm backend python -m unittest discover -s tests -v
docker compose run --rm sandbox-service python -m unittest discover -s tests -v
docker compose run --rm evaluation-worker python -m unittest -v test_worker
docker compose run --rm -e RUN_DOCKER_SANDBOX_INTEGRATION=1 sandbox-service python -m unittest tests.test_docker_integration -v
docker compose run --rm frontend-check
docker compose run --rm evals
```

真实 DashScope 合约测试默认跳过；需要时设置 `RUN_DASHSCOPE_INTEGRATION=1` 后单独执行。确定性回归测试不依赖外部模型稳定性。

## Day 10 安全回归矩阵

| 验收项 | 自动化证据 |
|---|---|
| 文档幂等 | `test_document_upload_replays_the_original_response_once` |
| embedding 重试 | `test_failed_embedding_job_can_be_reset_once_for_retry` |
| 权限过滤 | `test_retrieval_filters_chunks_by_permission_subject` |
| prompt injection | `test_untrusted_document_instruction_cannot_authorize_side_effect` |
| 防幻觉 | `test_unknown_citation_forces_human_handoff` |
| 审批幂等 | `test_high_risk_send_is_idempotent_and_resumes` |
| 沙箱危险命令 | `test_dangerous_command_is_rejected_before_create` |

## 项目结构

```text
backend/          FastAPI、Agent Loop、RAG、工具、记忆、迁移和测试
frontend/         Vue 3 前端，生产镜像提供已编译静态资源与 API 反向代理
worker/           Redis embedding job worker
evaluation_worker/ Ragas 异步评测 worker 与单元测试
sandbox_service/  Docker 一次性命令沙箱与隔离测试
evals/            live eval、RAG 黄金数据集与 runner
demo/             5 分钟演示文档
docs/             架构图、ER 图、状态机和讲稿
scripts/          Day 10 一键验收脚本
infra/            基础设施预留目录
```

## 设计文档与讲解材料

- [架构图、数据库关系图、状态机与完整时序](docs/ARCHITECTURE.md)
- [RAG 量化评测、数据契约与运行方式](docs/EVALUATION.md)
- [5 分钟 Demo 讲解稿](docs/DEMO_5_MIN.md)
- [2 分钟系统设计口述稿](docs/SYSTEM_DESIGN_2_MIN.md)
- [Day 10 完整性审查与最终总结](DAY10_SUMMARY.md)
- [Day 7 Docker 沙箱实现](DAY7_SUMMARY.md)
- [Day 6 工具与审批实现](DAY6_SUMMARY.md)
- [Day 5 长短期记忆实现](DAY5_SUMMARY.md)

## 当前边界

Day 10 已达到“单机一键启动、浏览器完成主流程、回归测试稳定、可用于面试演示”的目标，但不等同于生产就绪：

- 身份认证仍是演示级 header + 服务端角色映射，未接 OIDC/SSO。
- Outbox 只落通用消息记录，未接真实邮件、IM 或 Webhook 消费 Worker。
- 尚未提供 Prometheus/OpenTelemetry、集中日志、告警和 SLO。
- PostgreSQL、Redis、MinIO 和 Worker 仍是单实例，未做 HA、备份恢复演练和密钥托管。

这些边界不影响当前 Day 10 演示验收，但应作为生产化的下一阶段工作。
