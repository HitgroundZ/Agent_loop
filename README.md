# Agent Loop 知识库 MVP

当前版本完成到 Day 2：文档上传后会写入 MinIO、解析文本、自动切 chunk、创建 embedding job，并由 worker 异步调用千问向量模型写入 PostgreSQL pgvector。

- FastAPI 后端：文档上传、哈希去重、MinIO 对象存储、chunk 查询、embedding job 重试。
- PostgreSQL：使用 `pgvector/pgvector:pg16`，迁移会创建 `document_chunks` 和 `embedding_jobs`。
- Worker：从 Redis 队列消费 embedding job，批量调用 `text-embedding-v4`。
- Vue 3 前端：文档列表、状态展示、文本预览、chunk 文本/metadata/embedding 状态查看。

## 运行

复制示例环境变量文件：

```powershell
Copy-Item .env.example .env
```

在 `.env` 中配置百炼 API Key：

```text
DASHSCOPE_API_KEY=sk-ff93e976fa334cbf8d13d794a7b665d6
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

Day 1 的旧文档不会自动回填 chunk。若旧记录没有 MinIO object key 且没有 chunk，再次上传同 hash 文件时会替换为 Day 2 流程重新入库。

## API

主要接口：

- `POST /api/documents/upload`
- `GET /api/documents`
- `GET /api/documents/{document_id}`
- `GET /api/documents/{document_id}/chunks`
- `POST /api/documents/{document_id}/embedding-jobs`
- `DELETE /api/documents/{document_id}`

`chunks` 查询目前仍放在 `documents` router 内，因为它是文档的子资源；切片策略本身在 `backend/app/services/chunking.py`。

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
202607030002
```

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

## Day 2 验收

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

如果没有配置 `DASHSCOPE_API_KEY`，worker 会保留任务重试状态，并在日志中提示 `DASHSCOPE_API_KEY is not configured`。

## Windows 下的 Docker 说明

如果执行 `docker info` 时出现 `permission denied while trying to connect to ... docker_engine`，先启动 Docker Desktop，然后重新打开终端。

如果宿主机已经安装 PostgreSQL，并且占用了 `5432` 端口，需要先用管理员 PowerShell 停止并禁用本机 PostgreSQL 服务，再启动本项目：

```powershell
Stop-Service postgresql-x64-18
Set-Service postgresql-x64-18 -StartupType Disabled
```
