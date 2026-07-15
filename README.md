# Agent Loop 知识库 MVP

当前版本完成 Day 5 / Day 6 合并交付：在文档入库、混合检索和 Day 4 trace 的基础上，加入长短期记忆、LLM Function Calling、统一 Tool Registry、真实 rerank、风险策略、幂等人工审批和自动续跑。

- FastAPI 后端：文档上传、哈希去重、MinIO 对象存储、chunk 查询、embedding job 重试、检索接口。
- PostgreSQL：使用 `pgvector/pgvector:pg16`，保存 chunk 原文、`vector(1024)`、FTS `search_vector` 和 metadata filter 字段。
- Worker：从 Redis 队列消费 embedding job，批量调用 `text-embedding-v4`。
- Vue 3 前端：总览、Agent、审批台、长期记忆、知识库和检索实验室六个独立模块。

## 运行

复制示例环境变量文件：

```powershell
Copy-Item .env.example .env
```

在 `.env` 中配置百炼 API Key：

```text
DASHSCOPE_API_KEY=<your-rotated-local-key>
```

启动完整服务：

```powershell
docker-compose up -d --build
```

如果使用新版 Docker Compose，也可以运行：

```powershell
docker compose up -d --build
```

访问地址：

- 前端：http://localhost:5173
- 后端健康检查：http://localhost:8000/api/health
- 后端接口文档：http://localhost:8000/docs
- MinIO Console：http://localhost:9001

默认 MinIO 登录信息来自 `.env.example`：

- 用户名：`agent_loop`
- 密码：`agent_loop_password`
- bucket：`agent-loop-documents`

## 存储与索引流程

上传成功后：

1. 后端计算 SHA-256，用于去重。
2. 原始文件写入 MinIO：`documents/{source_hash}/source{ext}`。
3. 解析后的全文写入 MinIO：`documents/{document_id}/versions/{version_id}/extracted.txt`。
4. 数据库只保存对象 key、文本预览、metadata、chunk 文本和 embedding，不再保存 `documents.extracted_text`。
5. 后端按标题/段落优先切 chunk，超长内容再按字符窗口切片。
6. 后端创建幂等 embedding job 并推入 Redis 队列。
7. worker 批量调用 `text-embedding-v4`，默认 `dimensions=1024`，写入 `document_chunks.embedding vector(1024)`。
8. 检索接口可按 tenant、workspace、document、tags、时间和权限 subject 过滤 chunk。

Day 1 的旧文档不会自动回填 chunk。若旧记录没有 MinIO object key 且没有 chunk，再次上传同 hash 文件时会替换为 Day 2 流程重新入库。

## API

主要接口：

- `POST /api/documents/upload`
- `GET /api/documents`
- `GET /api/documents/{document_id}`
- `GET /api/documents/{document_id}/chunks`
- `POST /api/documents/{document_id}/embedding-jobs`
- `DELETE /api/documents/{document_id}`
- `POST /api/retrieval/search`
- `POST /api/retrieval/compare`
- `POST /api/agent/runs`
- `GET /api/agent/runs/{run_id}`
- `GET /api/agent/sessions/{session_id}`
- `GET /api/tool-actions`
- `GET /api/tool-actions/{action_id}`
- `POST /api/tool-actions/{action_id}/approve`
- `POST /api/tool-actions/{action_id}/reject`
- `GET /api/memories?user_id={user_id}`
- `GET /api/memories/messages?user_id={user_id}`
- `PATCH /api/memories/{memory_id}`
- `POST /api/memories/{memory_id}/corrections`
- `DELETE /api/memories/{memory_id}`

`chunks` 查询目前仍放在 `documents` router 内，因为它是文档的子资源；切片策略本身在 `backend/app/services/chunking.py`。

检索请求示例：

```json
{
  "query": "文档里怎么描述向量入库？",
  "strategy": "hybrid",
  "top_k": 8,
  "filters": {
    "tenant_id": "default",
    "workspace_id": "default",
    "tags": ["rag"],
    "principal": "team-a"
  }
}
```

`strategy=keyword` 只依赖 PostgreSQL FTS/ILIKE；`strategy=vector` 和 `hybrid` 需要配置 `DASHSCOPE_API_KEY` 来生成查询向量。当前 Day 3 不生成回答，只返回引用；无可靠来源时 `need_human_handoff=true`。

Agent 请求示例（模型自行判断是否检索）：

```json
{
  "question": "请记住我是一名 AI 工程师，并且偏好简洁回答。",
  "user_id": "demo-user",
  "session_id": "session-a",
  "strategy": "hybrid",
  "retrieval_mode": "auto",
  "top_k": 5,
  "auto_approve": true
}
```

`retrieval_mode` 可为 `auto | always | never`。`auto_approve` 不能绕过高风险审批。审批接口必须同时携带服务端映射为 approver 的 `X-Principal-Id` 和幂等键：

```powershell
$headers = @{
  'X-Principal-Id' = 'demo-user'
  'Idempotency-Key' = [guid]::NewGuid().ToString()
}
Invoke-RestMethod `
  -Uri 'http://localhost:8000/api/tool-actions/<action_id>/approve' `
  -Method Post `
  -Headers $headers `
  -ContentType 'application/json' `
  -Body '{"reason":"已确认目标与影响"}'
```

## 数据库迁移

项目使用 Alembic 管理数据库表结构。启动完整服务时，`migrate` 服务会先执行：

```powershell
docker-compose run --rm migrate
```

宿主机直接运行 Alembic 时：

```powershell
cd backend
alembic upgrade head
```

当前迁移版本应为：

```text
202607070001
```

本地角色、模型、rerank 和 Webhook 安全边界均通过 `.env` 配置。`TOOL_ROLE_ASSIGNMENTS` 只在服务端读取；不要把角色或权限放入 Agent 请求体。

## Day 5 / Day 6 验收测试

```powershell
docker-compose build backend
docker-compose run --rm backend python -m unittest discover -s tests -v
cd frontend
npm run build
```

真实 DashScope 测试默认跳过；明确需要时设置 `RUN_DASHSCOPE_INTEGRATION=1` 和 `DASHSCOPE_API_KEY` 后单独运行。结构和数据检查方式见 [backend/tests/README.md](./backend/tests/README.md)，实现总结见 [DAY5_SUMMARY.md](./DAY5_SUMMARY.md) 与 [DAY6_SUMMARY.md](./DAY6_SUMMARY.md)。

## 本地运行后端

后端直接跑在宿主机时，默认连接 `127.0.0.1:5432` 的 PostgreSQL。MinIO 也需要使用宿主机可访问地址，例如：

```text
MINIO_ENDPOINT=127.0.0.1:9000
```

安装并启动：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Day 2/Day 3 验收

分别上传以下类型的文件：

- PDF
- DOCX
- Markdown
- HTML

预期结果：

- 文档列表能显示解析、切片、向量化状态。
- 重复上传会通过 SHA-256 哈希识别。
- 选中文档后，可以查看文本预览、解析 metadata、chunk 文本、chunk metadata 和 embedding 状态。
- `document_chunks` 表有 chunk 数据，向量化成功后 `embedding` 不为空。
- 删除文档后，对应版本、chunk、embedding job 和 MinIO 对象被清理。
- 使用上传表单传入 tenant/workspace/tags/permissions 后，document 和 chunk 都会带上相同过滤字段。
- 在前端检索区可分别运行向量检索、关键词检索、混合检索，也可以点击对比同时查看三组结果。
- 检索结果包含文档名、切片编号、页码/标题、得分、摘要和元数据；无结果时显示“需人工处理”。

如果没有配置 `DASHSCOPE_API_KEY`，worker 会保留任务重试状态，并在日志中提示“尚未配置 DASHSCOPE_API_KEY”。

## Windows 下的 Docker 说明

如果执行 `docker info` 时出现 `permission denied while trying to connect to ... docker_engine`，先启动 Docker Desktop，然后重新打开终端。

如果宿主机已经安装 PostgreSQL，并且占用了 `5432` 端口，需要先用管理员 PowerShell 停止并禁用本机 PostgreSQL 服务，再启动本项目：

```powershell
Stop-Service postgresql-x64-18
Set-Service postgresql-x64-18 -StartupType Disabled
```
