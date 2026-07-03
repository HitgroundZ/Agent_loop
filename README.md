# Agent Loop 知识库 MVP

Day 1 主要完成项目基础结构和文档导入 MVP：

- FastAPI 后端：支持文档上传、哈希去重、解析状态和提取文本预览。
- Vue 3 前端：支持上传、文档列表、状态展示、元数据和提取文本查看。
- Docker Compose 中包含 PostgreSQL、pgvector 扩展、Redis、后端、前端和 worker。
- 预留轻量级 worker，为 Day 2 的异步 embedding 任务做准备。

## 运行

如果需要覆盖默认配置，可以复制示例环境变量文件：

```powershell
Copy-Item .env.example .env
```

启动完整服务：

```powershell
docker compose up --build
```

访问地址：

- 前端：http://localhost:5173
- 后端健康检查：http://localhost:8000/api/health
- 后端接口文档：http://localhost:8000/docs

## 本地运行后端

后端要求使用 PostgreSQL。直接在宿主机运行后端时，需要通过宿主机映射端口连接 Docker 里的 PostgreSQL 容器：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:DATABASE_URL = "postgresql+psycopg://agent_loop:agent_loop@127.0.0.1:15432/agent_loop"
uvicorn app.main:app --reload
```

上传文件会写入 `backend/storage/`。

在 Docker Compose 内部，backend 和 worker 使用 `postgres:5432` 连接数据库。其中 `postgres` 是 Compose 网络里的 PostgreSQL 服务名，`5432` 是容器内部端口。

从宿主机访问数据库时，例如 DataGrip 或本地直接运行 `uvicorn`，使用 `127.0.0.1:15432`。这里的 `15432` 是发布到宿主机的端口，对应容器内部的 `5432`。

## Day 1 验收

分别上传以下类型的文件：

- PDF
- DOCX
- Markdown
- HTML

预期结果：

- 文档列表能显示解析状态。
- 重复上传会通过 SHA-256 哈希识别。
- 选中文档后，可以查看提取文本、解析元数据，以及解析失败时的错误信息。

## Windows 下的 Docker 说明

如果执行 `docker info` 时出现 `permission denied while trying to connect to ... docker_engine`，先启动 Docker Desktop，然后重新打开终端。

如果问题仍然存在，可以把当前 Windows 用户加入 `docker-users` 用户组，或者使用具备 Docker 权限的终端运行命令。
