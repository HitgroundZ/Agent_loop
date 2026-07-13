from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any, Literal

from openai import OpenAI
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import Settings


Strategy = Literal["vector", "keyword", "hybrid"]


class RetrievalConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class RetrievalFilters:
    tenant_id: str | None = None
    workspace_id: str | None = None
    document_id: str | None = None
    document_ids: list[str] | None = None
    tags: list[str] | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None
    principal: str | None = None
    permission_subjects: list[str] | None = None


class QueryRewriter:
    def rewrite(self, query: str) -> str:
        return re.sub(r"\s+", " ", query).strip()


class TopKPolicy:
    def resolve(self, query: str, top_k: int | None) -> int:
        if top_k is not None:
            return max(1, min(int(top_k), 50))
        length = len(query)
        if length <= 20:
            return 5
        if length <= 80:
            return 8
        return 12


class NoopReranker:
    def rerank(self, results: list[dict], enabled: bool) -> tuple[list[dict], dict]:
        return results, {
            "rerank_requested": bool(enabled),
            "rerank_applied": False,
            "rerank_model": None,
        }


class RetrievalService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.rewriter = QueryRewriter()
        self.top_k_policy = TopKPolicy()
        self.reranker = NoopReranker()

    def search(
        self,
        db: Session,
        query: str,
        strategy: Strategy,
        filters: RetrievalFilters | None = None,
        top_k: int | None = None,
        rerank: bool = False,
    ) -> dict:
        rewritten_query = self.rewriter.rewrite(query)
        resolved_top_k = self.top_k_policy.resolve(rewritten_query, top_k)
        filters = filters or RetrievalFilters()

        diagnostics: dict[str, Any] = {
            "query_length": len(rewritten_query),
            "rerank": {},
        }
        if not rewritten_query:
            return self._response(query, rewritten_query, strategy, resolved_top_k, [], diagnostics)

        if strategy == "vector":
            results = self._vector_search(db, rewritten_query, filters, resolved_top_k)
            diagnostics["vector_candidates"] = len(results)
        elif strategy == "keyword":
            results = self._keyword_search(db, rewritten_query, filters, resolved_top_k)
            diagnostics["keyword_candidates"] = len(results)
        elif strategy == "hybrid":
            candidate_limit = max(resolved_top_k * 4, 20)
            vector_results = self._vector_search(db, rewritten_query, filters, candidate_limit)
            keyword_results = self._keyword_search(db, rewritten_query, filters, candidate_limit)
            results = self._rrf(vector_results, keyword_results, resolved_top_k)
            diagnostics["vector_candidates"] = len(vector_results)
            diagnostics["keyword_candidates"] = len(keyword_results)
            diagnostics["rrf_k"] = 60
        else:
            raise ValueError(f"不支持的检索策略：{strategy}")

        results, rerank_diagnostics = self.reranker.rerank(results, rerank)
        diagnostics["rerank"] = rerank_diagnostics
        return self._response(query, rewritten_query, strategy, resolved_top_k, results, diagnostics)

    def _vector_search(
        self,
        db: Session,
        query: str,
        filters: RetrievalFilters,
        limit: int,
    ) -> list[dict]:
        embedding = self._embed_query(query)
        where_sql, params = _filter_sql(filters, prefix="c")
        params.update(
            {
                "embedding": _vector_literal(embedding),
                "limit": limit,
            }
        )
        sql = f"""
            SELECT
                c.id AS chunk_id,
                c.document_id,
                d.filename AS document_name,
                c.chunk_index,
                c.page,
                c.heading,
                c.text AS chunk_text,
                c.metadata AS chunk_metadata,
                c.tenant_id,
                c.workspace_id,
                c.tags,
                c.permissions,
                c.created_at,
                1 - (c.embedding <=> CAST(:embedding AS vector)) AS raw_score,
                c.embedding <=> CAST(:embedding AS vector) AS distance
            FROM document_chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.embedding IS NOT NULL
              AND c.embedding_status = 'embedded'
              {where_sql}
            ORDER BY c.embedding <=> CAST(:embedding AS vector)
            LIMIT :limit
        """
        rows = db.execute(text(sql), params).mappings().all()
        return [_candidate(row, query, "vector", index + 1) for index, row in enumerate(rows)]

    def _keyword_search(
        self,
        db: Session,
        query: str,
        filters: RetrievalFilters,
        limit: int,
    ) -> list[dict]:
        rows = self._fts_search(db, query, filters, limit)
        if not rows or _contains_cjk(query):
            fallback_rows = self._like_search(db, query, filters, limit)
            rows = _merge_rows(rows, fallback_rows, limit)
        return [_candidate(row, query, "keyword", index + 1) for index, row in enumerate(rows)]

    def _fts_search(self, db: Session, query: str, filters: RetrievalFilters, limit: int) -> list[dict]:
        where_sql, params = _filter_sql(filters, prefix="c")
        params.update({"query": query, "limit": limit})
        sql = f"""
            WITH q AS (SELECT websearch_to_tsquery('simple', :query) AS tsq)
            SELECT
                c.id AS chunk_id,
                c.document_id,
                d.filename AS document_name,
                c.chunk_index,
                c.page,
                c.heading,
                c.text AS chunk_text,
                c.metadata AS chunk_metadata,
                c.tenant_id,
                c.workspace_id,
                c.tags,
                c.permissions,
                c.created_at,
                ts_rank_cd(c.search_vector, q.tsq) AS raw_score,
                NULL::double precision AS distance
            FROM document_chunks c
            JOIN documents d ON d.id = c.document_id
            CROSS JOIN q
            WHERE c.search_vector @@ q.tsq
              {where_sql}
            ORDER BY raw_score DESC, c.created_at DESC
            LIMIT :limit
        """
        return list(db.execute(text(sql), params).mappings().all())

    def _like_search(self, db: Session, query: str, filters: RetrievalFilters, limit: int) -> list[dict]:
        where_sql, params = _filter_sql(filters, prefix="c")
        params.update({"like_query": f"%{query}%", "limit": limit})
        sql = f"""
            SELECT
                c.id AS chunk_id,
                c.document_id,
                d.filename AS document_name,
                c.chunk_index,
                c.page,
                c.heading,
                c.text AS chunk_text,
                c.metadata AS chunk_metadata,
                c.tenant_id,
                c.workspace_id,
                c.tags,
                c.permissions,
                c.created_at,
                (
                    CASE WHEN c.heading ILIKE :like_query THEN 0.6 ELSE 0 END
                    + CASE WHEN c.text ILIKE :like_query THEN 0.4 ELSE 0 END
                ) AS raw_score,
                NULL::double precision AS distance
            FROM document_chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE (c.heading ILIKE :like_query OR c.text ILIKE :like_query)
              {where_sql}
            ORDER BY raw_score DESC, c.created_at DESC
            LIMIT :limit
        """
        return list(db.execute(text(sql), params).mappings().all())

    def _rrf(self, vector_results: list[dict], keyword_results: list[dict], top_k: int) -> list[dict]:
        fused: dict[str, dict] = {}
        for source_name, results in (("vector", vector_results), ("keyword", keyword_results)):
            for rank, result in enumerate(results, start=1):
                chunk_id = result["chunk_id"]
                if chunk_id not in fused:
                    fused[chunk_id] = {**result, "score": 0.0, "source_scores": {}}
                fused[chunk_id]["score"] += 1 / (60 + rank)
                fused[chunk_id]["source_scores"][source_name] = result["score"]
                fused[chunk_id]["retrieval_source"] = "hybrid"
        ranked = sorted(fused.values(), key=lambda item: item["score"], reverse=True)
        for index, result in enumerate(ranked, start=1):
            result["rank"] = index
        return ranked[:top_k]

    def _embed_query(self, query: str) -> list[float]:
        if not self.settings.dashscope_api_key:
            raise RetrievalConfigurationError("尚未配置 DASHSCOPE_API_KEY")
        client = OpenAI(
            api_key=self.settings.dashscope_api_key,
            base_url=self.settings.dashscope_base_url,
        )
        response = client.embeddings.create(
            model=self.settings.embedding_model,
            input=query,
            dimensions=self.settings.embedding_dim,
            encoding_format="float",
        )
        embedding = response.data[0].embedding
        if len(embedding) != self.settings.embedding_dim:
            raise RetrievalConfigurationError(
                f"向量维度不匹配：期望 {self.settings.embedding_dim}，实际 {len(embedding)}"
            )
        return embedding

    def _response(
        self,
        query: str,
        rewritten_query: str,
        strategy: Strategy,
        top_k: int,
        results: list[dict],
        diagnostics: dict,
    ) -> dict:
        return {
            "query": query,
            "rewritten_query": rewritten_query,
            "strategy": strategy,
            "top_k": top_k,
            "need_human_handoff": len(results) == 0,
            "results": results,
            "diagnostics": diagnostics,
        }


def _filter_sql(filters: RetrievalFilters, prefix: str) -> tuple[str, dict]:
    clauses: list[str] = []
    params: dict[str, Any] = {}

    if filters.tenant_id:
        clauses.append(f"AND {prefix}.tenant_id = :tenant_id")
        params["tenant_id"] = filters.tenant_id
    if filters.workspace_id:
        clauses.append(f"AND {prefix}.workspace_id = :workspace_id")
        params["workspace_id"] = filters.workspace_id

    document_ids = list(filters.document_ids or [])
    if filters.document_id:
        document_ids.append(filters.document_id)
    document_ids = [item for item in dict.fromkeys(document_ids) if item]
    if document_ids:
        placeholders = []
        for index, document_id in enumerate(document_ids):
            key = f"document_id_{index}"
            placeholders.append(f":{key}")
            params[key] = document_id
        clauses.append(f"AND {prefix}.document_id IN ({', '.join(placeholders)})")

    for index, tag in enumerate(filters.tags or []):
        key = f"tag_{index}"
        clauses.append(f"AND {prefix}.tags ? :{key}")
        params[key] = tag

    if filters.created_from:
        clauses.append(f"AND {prefix}.created_at >= :created_from")
        params["created_from"] = filters.created_from
    if filters.created_to:
        clauses.append(f"AND {prefix}.created_at <= :created_to")
        params["created_to"] = filters.created_to

    subjects = _permission_subjects(filters)
    subjects_json = (
        f"CASE WHEN jsonb_typeof({prefix}.permissions->'subjects') = 'array' "
        f"THEN {prefix}.permissions->'subjects' ELSE '[]'::jsonb END"
    )
    permission_public_clause = (
        f"{prefix}.permissions = '{{}}'::jsonb "
        f"OR NOT ({prefix}.permissions ? 'subjects') "
        f"OR jsonb_array_length({subjects_json}) = 0"
    )
    subject_clauses = []
    for index, subject in enumerate(subjects):
        key = f"permission_subject_{index}"
        subject_clauses.append(f"({subjects_json}) ? :{key}")
        params[key] = subject
    permission_parts = [permission_public_clause, *subject_clauses]
    clauses.append(f"AND ({' OR '.join(permission_parts)})")

    return "\n              " + "\n              ".join(clauses) if clauses else "", params


def _permission_subjects(filters: RetrievalFilters) -> list[str]:
    subjects: list[str] = []
    if filters.principal:
        subjects.append(filters.principal)
    subjects.extend(filters.permission_subjects or [])
    return [subject for subject in dict.fromkeys(subjects) if subject]


def _candidate(row: Any, query: str, source: str, rank: int) -> dict:
    text_value = row["chunk_text"] or ""
    score = float(row["raw_score"] or 0)
    return {
        "document_id": row["document_id"],
        "document_name": row["document_name"],
        "chunk_id": row["chunk_id"],
        "chunk_index": row["chunk_index"],
        "page": row["page"],
        "heading": row["heading"],
        "score": score,
        "rank": rank,
        "snippet": _snippet(text_value, query),
        "metadata": {
            "chunk": row["chunk_metadata"] or {},
            "tenant_id": row["tenant_id"],
            "workspace_id": row["workspace_id"],
            "tags": row["tags"] or [],
        },
        "retrieval_source": source,
        "source_scores": {source: score},
        "distance": float(row["distance"]) if row["distance"] is not None else None,
    }


def _snippet(text_value: str, query: str, size: int = 280) -> str:
    compact = re.sub(r"\s+", " ", text_value).strip()
    if len(compact) <= size:
        return compact
    lowered = compact.lower()
    terms = [query.lower(), *[term.lower() for term in re.split(r"\s+", query) if len(term) > 1]]
    positions = [lowered.find(term) for term in terms if term and lowered.find(term) >= 0]
    center = min(positions) if positions else 0
    start = max(0, center - size // 3)
    end = min(len(compact), start + size)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(compact) else ""
    return f"{prefix}{compact[start:end]}{suffix}"


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(str(float(value)) for value in values) + "]"


def _contains_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def _merge_rows(primary: list[Any], fallback: list[Any], limit: int) -> list[Any]:
    merged: dict[str, Any] = {}
    for row in [*primary, *fallback]:
        chunk_id = row["chunk_id"]
        if chunk_id not in merged or (row["raw_score"] or 0) > (merged[chunk_id]["raw_score"] or 0):
            merged[chunk_id] = row
    return sorted(merged.values(), key=lambda row: row["raw_score"] or 0, reverse=True)[:limit]
