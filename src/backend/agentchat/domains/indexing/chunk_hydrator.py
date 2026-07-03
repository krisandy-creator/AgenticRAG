from __future__ import annotations

import json
from typing import Any

from agentchat.domains.indexing.entities import RetrievalCandidate, SearchHit
from agentchat.domains.indexing.repositories import ChunkRepository
from agentchat.infrastructure.search_store.elasticsearch_store import ElasticsearchChunkStore


class ChunkHydrator:
    """根据 chunk_id 补全内容与文档元数据。"""

    def __init__(self):
        self.sparse_store = ElasticsearchChunkStore()
        self.last_debug: dict[str, Any] = {}

    async def hydrate(self, candidates: list[RetrievalCandidate]) -> list[SearchHit]:
        if not candidates:
            self.last_debug = {
                "requested_ids": [],
                "mget_ids": [],
                "hydrated_count": 0,
                "missing_content_ids": [],
                "preview_fallback_count": 0,
            }
            return []

        chunk_ids = [candidate.chunk_id for candidate in candidates]
        rows = ChunkRepository.list_chunks_by_ids(chunk_ids)
        row_map = {chunk.chunk_id: (chunk, document) for chunk, document in rows}

        contentless_ids = [
            candidate.chunk_id
            for candidate in candidates
            if not (candidate.content or "").strip()
        ]
        es_contents = await self.sparse_store.get_contents(contentless_ids) if contentless_ids else {}

        hits: list[SearchHit] = []
        hydrated_count = 0
        preview_fallback_count = 0
        missing_content_ids: list[str] = []

        for candidate in candidates:
            row = row_map.get(candidate.chunk_id)
            if not row:
                missing_content_ids.append(candidate.chunk_id)
                continue

            chunk, document = row
            metadata = self._load_metadata(chunk.metadata_json)
            content = self._resolve_content(candidate, chunk, es_contents)

            if not content:
                missing_content_ids.append(chunk.chunk_id)
                continue

            if not (candidate.content or "").strip() and content:
                if content == (chunk.content or "").strip():
                    preview_fallback_count += 1
                else:
                    hydrated_count += 1

            public_metadata = self._public_metadata(metadata)
            if not (candidate.content or "").strip() and chunk.chunk_id in es_contents:
                public_metadata = {**public_metadata, "content_source": "elasticsearch"}

            hits.append(
                SearchHit(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    file_name=document.file_name,
                    file_type=document.file_type,
                    content=content,
                    page_no=chunk.page_no,
                    chunk_type=chunk.chunk_type,
                    permission_level=chunk.permission_level,
                    score=candidate.rrf_score,
                    metadata=public_metadata,
                    score_breakdown={
                        "rrf": round(candidate.rrf_score, 6),
                        "sparse": round(candidate.sparse_score, 6),
                        "dense": round(candidate.dense_score, 6),
                        "sparse_rank": float(candidate.sparse_rank or 0),
                        "dense_rank": float(candidate.dense_rank or 0),
                    },
                    rank_sources=self._rank_sources(candidate),
                    rank=candidate.fusion_rank,
                )
            )

        self.last_debug = {
            "requested_ids": chunk_ids,
            "mget_ids": contentless_ids,
            "hydrated_count": hydrated_count,
            "missing_content_ids": missing_content_ids,
            "preview_fallback_count": preview_fallback_count,
        }
        return hits

    @classmethod
    def _resolve_content(
        cls,
        candidate: RetrievalCandidate,
        chunk,
        es_contents: dict[str, str],
    ) -> str:
        content = (candidate.content or "").strip()
        if content:
            return content

        content = es_contents.get(chunk.chunk_id, "").strip()
        if content:
            return content

        preview = (chunk.content or "").strip()
        if preview and len(preview) < ElasticsearchChunkStore.CONTENT_PREVIEW_LIMIT:
            return preview
        return ""

    @staticmethod
    def _load_metadata(metadata_json: str | None) -> dict:
        try:
            return json.loads(metadata_json or "{}")
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _public_metadata(metadata: dict) -> dict:
        return {
            key: value
            for key, value in metadata.items()
            if key not in {"embedding", "embedding_vector", "vector"}
        }

    @staticmethod
    def _rank_sources(candidate: RetrievalCandidate) -> list[str]:
        sources = []
        if candidate.sparse_rank:
            sources.append("sparse")
        if candidate.dense_rank:
            sources.append("dense")
        if candidate.sparse_rank and candidate.dense_rank:
            sources.append("rrf")
        return sources
