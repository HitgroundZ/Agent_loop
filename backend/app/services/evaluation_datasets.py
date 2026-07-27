from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Document, DocumentChunk
from app.services.retrieval import context_id_for_text


class EvaluationDatasetError(ValueError):
    pass


@dataclass(frozen=True)
class EvaluationDataset:
    manifest: dict[str, Any]
    cases: list[dict[str, Any]]
    path: Path

    @property
    def dataset_id(self) -> str:
        return str(self.manifest["id"])

    @property
    def version(self) -> str:
        return str(self.manifest["version"])


def list_evaluation_datasets(dataset_dir: str, db: Session | None = None) -> list[dict]:
    root = Path(dataset_dir).resolve()
    if not root.exists():
        return []
    datasets: list[dict] = []
    for manifest_path in sorted(root.glob("*/manifest.json")):
        try:
            dataset = _load_from_manifest(root, manifest_path)
            errors = validate_dataset_against_knowledge_base(db, dataset) if db else []
            datasets.append(dataset_summary(dataset, errors))
        except (EvaluationDatasetError, OSError, json.JSONDecodeError) as exc:
            datasets.append({
                "id": manifest_path.parent.name,
                "name": manifest_path.parent.name,
                "version": None,
                "case_count": 0,
                "valid": False,
                "validation_errors": [str(exc)],
            })
    return datasets


def load_evaluation_dataset(dataset_dir: str, dataset_id: str) -> EvaluationDataset:
    if not dataset_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in dataset_id):
        raise EvaluationDatasetError("数据集 ID 只允许字母、数字、短横线和下划线")
    root = Path(dataset_dir).resolve()
    manifest_path = (root / dataset_id / "manifest.json").resolve()
    if root not in manifest_path.parents or not manifest_path.is_file():
        raise EvaluationDatasetError(f"评测数据集不存在：{dataset_id}")
    return _load_from_manifest(root, manifest_path)


def validate_dataset_against_knowledge_base(
    db: Session | None,
    dataset: EvaluationDataset,
) -> list[str]:
    if db is None:
        return []
    errors: list[str] = []
    required_source_hashes = set((dataset.manifest.get("corpus") or {}).get("source_hashes") or [])
    if required_source_hashes:
        existing_source_hashes = set(
            db.scalars(select(Document.source_hash).where(Document.source_hash.in_(required_source_hashes))).all()
        )
        for source_hash in sorted(required_source_hashes - existing_source_hashes):
            errors.append(f"知识库缺少语料 source_hash：{source_hash}")

    reference_contexts = [
        context
        for case in dataset.cases
        for context in case["reference_contexts"]
    ]
    document_names = {str(context.get("document_name") or "") for context in reference_contexts}
    document_names.discard("")
    stmt = select(DocumentChunk.text)
    if document_names:
        stmt = stmt.join(Document, Document.id == DocumentChunk.document_id).where(
            Document.filename.in_(document_names)
        )
    existing_context_ids = {context_id_for_text(text) for text in db.scalars(stmt).all()}
    required_context_ids = {str(context["context_id"]) for context in reference_contexts}
    missing = sorted(required_context_ids - existing_context_ids)
    if missing:
        preview = ", ".join(missing[:3])
        suffix = "…" if len(missing) > 3 else ""
        errors.append(f"知识库缺少 {len(missing)} 个黄金上下文：{preview}{suffix}")
    return errors


def dataset_summary(dataset: EvaluationDataset, errors: list[str] | None = None) -> dict:
    validation_errors = errors or []
    manifest = dataset.manifest
    return {
        "id": dataset.dataset_id,
        "name": manifest.get("name") or dataset.dataset_id,
        "version": dataset.version,
        "description": manifest.get("description") or "",
        "case_count": len(dataset.cases),
        "default_top_k": int(manifest.get("default_top_k") or 5),
        "tags": manifest.get("tags") or [],
        "corpus": manifest.get("corpus") or {},
        "valid": not validation_errors,
        "validation_errors": validation_errors,
    }


def _load_from_manifest(root: Path, manifest_path: Path) -> EvaluationDataset:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for field in ("id", "name", "version"):
        if not str(manifest.get(field) or "").strip():
            raise EvaluationDatasetError(f"manifest 缺少字段：{field}")
    if manifest["id"] != manifest_path.parent.name:
        raise EvaluationDatasetError("manifest.id 必须与数据集目录名一致")

    cases_name = str(manifest.get("cases_file") or "cases.jsonl")
    cases_path = (manifest_path.parent / cases_name).resolve()
    if root not in cases_path.parents or not cases_path.is_file():
        raise EvaluationDatasetError(f"案例文件不存在：{cases_name}")
    cases: list[dict] = []
    case_ids: set[str] = set()
    for line_number, line in enumerate(cases_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvaluationDatasetError(f"cases.jsonl 第 {line_number} 行不是合法 JSON") from exc
        _validate_case(case, line_number)
        case_id = str(case["case_id"])
        if case_id in case_ids:
            raise EvaluationDatasetError(f"案例 ID 重复：{case_id}")
        case_ids.add(case_id)
        cases.append(case)
    if not cases:
        raise EvaluationDatasetError("数据集至少需要一个案例")
    declared_count = manifest.get("case_count")
    if declared_count is not None and int(declared_count) != len(cases):
        raise EvaluationDatasetError(
            f"manifest.case_count={declared_count}，实际案例数={len(cases)}"
        )
    return EvaluationDataset(manifest=manifest, cases=cases, path=manifest_path.parent)


def _validate_case(case: dict, line_number: int) -> None:
    prefix = f"cases.jsonl 第 {line_number} 行"
    for field in ("case_id", "question", "reference_answer"):
        if not isinstance(case.get(field), str) or not case[field].strip():
            raise EvaluationDatasetError(f"{prefix} 缺少非空字段：{field}")
    contexts = case.get("reference_contexts")
    if not isinstance(contexts, list) or not contexts:
        raise EvaluationDatasetError(f"{prefix} 至少需要一个 reference_context")
    for context in contexts:
        if not isinstance(context, dict):
            raise EvaluationDatasetError(f"{prefix} 的 reference_context 必须是对象")
        context_id = str(context.get("context_id") or "")
        if not context_id.startswith("sha256:") or len(context_id) != 71:
            raise EvaluationDatasetError(f"{prefix} 包含非法 context_id")
        if not str(context.get("text") or "").strip():
            raise EvaluationDatasetError(f"{prefix} 的 reference_context 缺少 text")
    filters = case.get("filters")
    if filters is not None and not isinstance(filters, dict):
        raise EvaluationDatasetError(f"{prefix} 的 filters 必须是对象")
