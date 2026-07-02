# Agent Loop Knowledge Base MVP

Day 1 focuses on the project foundation and document ingestion MVP:

- FastAPI backend with document upload, hash deduplication, parsing status, and extracted text preview.
- Vue 3 frontend for upload, document list, status, metadata, and extracted text.
- PostgreSQL with pgvector extension, Redis, backend, frontend, and worker in Docker Compose.
- A lightweight worker placeholder so the Day 2 async embedding job has a home.

## Run

Copy the example environment file if you want to override defaults:

```powershell
Copy-Item .env.example .env
```

Start the stack:

```powershell
docker compose up --build
```

Open:

- Frontend: http://localhost:5173
- Backend health: http://localhost:8000/api/health
- Backend docs: http://localhost:8000/docs

## Local Backend Fallback

If Docker is temporarily unavailable, the backend can use local SQLite for Day 1 parsing:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

The SQLite database and uploaded files will be written under `backend/storage/`.

## Day 1 Acceptance

Upload one file of each type:

- PDF
- DOCX
- Markdown
- HTML

Expected result:

- The document list shows parsing status.
- Duplicate uploads are detected by SHA-256 hash.
- Selecting a document shows extracted text, parser metadata, and error details if parsing failed.

## Docker Note On Windows

If `docker info` shows `permission denied while trying to connect to ... docker_engine`, start Docker Desktop and reopen the terminal. If it still fails, add the current Windows user to the `docker-users` group or run the terminal with the required Docker permission.
