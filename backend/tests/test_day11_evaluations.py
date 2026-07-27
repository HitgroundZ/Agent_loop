from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.main import app
from app.models import (
    Document,
    DocumentChunk,
    DocumentVersion,
    EvaluationRun,
    IdempotencyRecord,
    new_id,
)
from app.services.evaluation_datasets import (
    EvaluationDatasetError,
    load_evaluation_dataset,
)
from app.services.retrieval import context_id_for_text


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://agent_loop:agent_loop@127.0.0.1:5432/agent_loop",
)


class EvaluationDatasetUnitTest(unittest.TestCase):
    def test_context_id_normalizes_whitespace(self) -> None:
        self.assertEqual(context_id_for_text("第一段\n\n第二段"), context_id_for_text("第一段 第二段"))
        self.assertNotEqual(context_id_for_text("第一段"), context_id_for_text("另一段"))

    def test_duplicate_case_ids_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "duplicate"
            dataset.mkdir()
            (dataset / "manifest.json").write_text(json.dumps({
                "id": "duplicate", "name": "duplicate", "version": "1", "case_count": 2,
            }), encoding="utf-8")
            context = {
                "context_id": context_id_for_text("gold"),
                "document_name": "gold.md",
                "text": "gold",
            }
            case = {
                "case_id": "same", "question": "问题", "reference_answer": "答案",
                "reference_contexts": [context],
            }
            (dataset / "cases.jsonl").write_text(
                json.dumps(case, ensure_ascii=False) + "\n" + json.dumps(case, ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(EvaluationDatasetError, "案例 ID 重复"):
                load_evaluation_dataset(str(root), "duplicate")


class EvaluationApiIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = create_engine(DATABASE_URL, pool_pre_ping=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dataset_id = f"eval-{uuid4().hex[:8]}"
        self.idempotency_key = f"eval-create-{uuid4()}"
        self.document_id = ""
        self.run_id = ""
        text = "## 评测上下文\n\n稳定上下文用于验证评测 API。"
        source_bytes = f"source-{uuid4()}".encode()
        source_hash = sha256(source_bytes).hexdigest()
        context_id = context_id_for_text(text)
        dataset_dir = Path(self.temp_dir.name) / self.dataset_id
        dataset_dir.mkdir()
        (dataset_dir / "manifest.json").write_text(json.dumps({
            "id": self.dataset_id,
            "name": "Evaluation API Test",
            "version": "1.0.0",
            "case_count": 1,
            "default_top_k": 5,
            "corpus": {"source_hashes": [source_hash]},
        }), encoding="utf-8")
        (dataset_dir / "cases.jsonl").write_text(json.dumps({
            "case_id": "api-case",
            "question": "评测上下文用于什么？",
            "reference_answer": "用于验证评测 API。",
            "reference_contexts": [{
                "context_id": context_id,
                "document_name": "evaluation-api.md",
                "text": text,
            }],
            "filters": {"tenant_id": "default", "workspace_id": "default"},
        }, ensure_ascii=False), encoding="utf-8")
        self.settings = Settings(database_url=DATABASE_URL, eval_dataset_dir=self.temp_dir.name)

        with Session(self.engine, expire_on_commit=False) as db:
            document = Document(
                id=new_id(), filename="evaluation-api.md", content_type="text/markdown",
                file_ext=".md", source_hash=source_hash, size_bytes=len(source_bytes),
                status="indexed", tenant_id="default", workspace_id="default",
                tags=[], permissions={}, metadata_json={},
            )
            version = DocumentVersion(
                id=new_id(), document=document, version_no=1, source_hash=source_hash,
                extracted_chars=len(text), metadata_json={},
            )
            chunk = DocumentChunk(
                id=new_id(), document=document, version=version, chunk_index=0,
                source_hash=source_hash, tenant_id="default", workspace_id="default",
                tags=[], permissions={}, text=text, metadata_json={}, embedding_status="embedded",
            )
            db.add_all([document, version, chunk])
            db.commit()
            self.document_id = document.id

    def tearDown(self) -> None:
        app.dependency_overrides.pop(get_settings, None)
        with Session(self.engine) as db:
            if self.run_id:
                db.execute(delete(EvaluationRun).where(EvaluationRun.id == self.run_id))
            db.execute(delete(IdempotencyRecord).where(IdempotencyRecord.key == self.idempotency_key))
            if self.document_id:
                db.execute(delete(Document).where(Document.id == self.document_id))
            db.commit()
        self.temp_dir.cleanup()

    def test_create_run_is_idempotent_and_visible_in_detail(self) -> None:
        app.dependency_overrides[get_settings] = lambda: self.settings
        headers = {"Idempotency-Key": self.idempotency_key}
        payload = {"dataset_id": self.dataset_id, "strategy": "hybrid", "top_k": 5}
        with patch("app.routers.evaluations.enqueue_evaluation_run", return_value=True), TestClient(app) as client:
            datasets = client.get("/api/evaluations/datasets")
            first = client.post("/api/evaluations/runs", json=payload, headers=headers)
            second = client.post("/api/evaluations/runs", json=payload, headers=headers)
            self.run_id = first.json().get("id", "")
            detail = client.get(f"/api/evaluations/runs/{self.run_id}")

        self.assertEqual(200, datasets.status_code, datasets.text)
        self.assertTrue(datasets.json()["items"][0]["valid"])
        self.assertEqual(202, first.status_code, first.text)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(200, detail.status_code, detail.text)
        self.assertEqual(self.dataset_id, detail.json()["dataset_id"])
        self.assertEqual(1, detail.json()["total_cases"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
