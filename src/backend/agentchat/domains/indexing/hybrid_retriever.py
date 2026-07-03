from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentchat.domains.indexing.entities import RetrievalCandidate
from agentchat.infrastructure.models.embedding_provider import EmbeddingProvider
from agentchat.infrastructure.search_store.elasticsearch_store import ElasticsearchChunkStore
from agentchat.infrastructure.vector_store.milvus_store import MilvusVectorStore


class HybridRetriever:
    """双路召回：Elasticsearch BM25 + Milvus 向量检索。"""

    DEFAULT_SPARSE_TOP_K = 100
    DEFAULT_DENSE_TOP_K = 100

    def __init__(self):
        self.embedding_provider = EmbeddingProvider()
        self.sparse_store = ElasticsearchChunkStore()
        self.dense_store = MilvusVectorStore()
        self.last_debug: dict[str, Any] = {}

    async def retrieve(
        self,
        queries: list[str],
        access_level: int,
        sparse_top_k: int = DEFAULT_SPARSE_TOP_K,
        dense_top_k: int = DEFAULT_DENSE_TOP_K,
    ) -> tuple[list[RetrievalCandidate], list[RetrievalCandidate]]:
        content_queries = [query.strip() for query in queries if query and query.strip()]
        sparse_candidates = await self._sparse_candidates(content_queries, access_level, sparse_top_k)
        dense_candidates = await self._dense_candidates(content_queries, access_level, dense_top_k)
        self.last_debug = {
            "queries": content_queries,
            "sparse_top_k": sparse_top_k,
            "dense_top_k": dense_top_k,
            "sparse_count": len(sparse_candidates),
            "dense_count": len(dense_candidates),
            "sparse_provider": self.sparse_store.last_provider,
            "dense_provider": self.dense_store.last_provider,
            "sparse_debug": self.sparse_store.last_debug,
            "dense_debug": self.dense_store.last_debug,
        }
        return sparse_candidates, dense_candidates

    async def _sparse_candidates(
        self,
        queries: list[str],
        access_level: int,
        top_k: int,
    ) -> list[RetrievalCandidate]:
        if not queries:
            return []

        merged: dict[str, RetrievalCandidate] = {}
        for query in queries:
            hits = await self.sparse_store.search(query, access_level=access_level, top_k=top_k)
            for rank, hit in enumerate(hits, start=1):
                existing = merged.get(hit.chunk_id)
                if existing is None or hit.score > existing.sparse_score:
                    merged[hit.chunk_id] = RetrievalCandidate(
                        chunk_id=hit.chunk_id,
                        document_id=hit.document_id,
                        sparse_score=hit.score,
                        sparse_rank=rank,
                        content=hit.content,
                    )
                elif existing.sparse_rank == 0 or rank < existing.sparse_rank:
                    existing.sparse_rank = rank
                    existing.sparse_score = max(existing.sparse_score, hit.score)

        candidates = list(merged.values())
        candidates.sort(key=lambda item: (-item.sparse_score, item.sparse_rank or 10**9))
        for rank, candidate in enumerate(candidates, start=1):
            candidate.sparse_rank = rank
        return candidates

    async def _dense_candidates(
        self,
        queries: list[str],
        access_level: int,
        top_k: int,
    ) -> list[RetrievalCandidate]:
        if not queries:
            return []

        embeddings = await self.embedding_provider.embed_texts(queries)
        if len(embeddings) != len(queries):
            return []

        merged: dict[str, RetrievalCandidate] = {}
        for query_vector in embeddings:
            hits = await self.dense_store.search(
                query_vector,
                filters={"access_level": access_level},
                top_k=top_k,
            )
            for rank, hit in enumerate(hits, start=1):
                chunk_id = str(hit.metadata.get("chunk_id") or "")
                document_id = str(hit.metadata.get("document_id") or "")
                if not chunk_id:
                    continue
                existing = merged.get(chunk_id)
                if existing is None or hit.score > existing.dense_score:
                    merged[chunk_id] = RetrievalCandidate(
                        chunk_id=chunk_id,
                        document_id=document_id,
                        vector_id=hit.vector_id,
                        dense_score=hit.score,
                        dense_rank=rank,
                    )
                elif existing.dense_rank == 0 or rank < existing.dense_rank:
                    existing.dense_rank = rank
                    existing.dense_score = max(existing.dense_score, hit.score)

        candidates = list(merged.values())
        candidates.sort(key=lambda item: (-item.dense_score, item.dense_rank or 10**9))
        for rank, candidate in enumerate(candidates, start=1):
            candidate.dense_rank = rank
        return candidates
