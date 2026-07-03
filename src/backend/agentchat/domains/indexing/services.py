from typing import Any

from loguru import logger

from agentchat.domains.indexing.candidate_fusion import CandidateFusionService
from agentchat.domains.indexing.chunk_hydrator import ChunkHydrator
from agentchat.domains.indexing.chunk_index_writer import ChunkIndexWriter
from agentchat.domains.indexing.chunking import TextChunk
from agentchat.domains.indexing.entities import DocumentChunkTable, SearchHit
from agentchat.domains.indexing.hybrid_retriever import HybridRetriever


class IndexingService:
    SPARSE_TOP_K = 100
    DENSE_TOP_K = 100

    def __init__(self):
        self.index_writer = ChunkIndexWriter()
        self.retriever = HybridRetriever()
        self.fusion = CandidateFusionService()
        self.hydrator = ChunkHydrator()
        self.last_debug: dict[str, Any] = {}

    async def index_chunks(self, document, chunks: list[TextChunk]) -> list[DocumentChunkTable]:
        return await self.index_chunks_batch(document, chunks, start_index=0, replace=True)

    async def index_chunks_batch(
        self,
        document,
        chunks: list[TextChunk],
        *,
        start_index: int = 0,
        replace: bool = False,
        embedding_batch_size: int = 32,
        embedding_max_concurrency: int = 5,
        embedding_api_batch_size: int = 10,
    ) -> list[DocumentChunkTable]:
        return await self.index_writer.index_chunks_batch(
            document,
            chunks,
            start_index=start_index,
            replace=replace,
            embedding_batch_size=embedding_batch_size,
            embedding_max_concurrency=embedding_max_concurrency,
            embedding_api_batch_size=embedding_api_batch_size,
        )

    async def search(
        self,
        query: str | list[str],
        access_level: int,
        top_k: int = 50,
        structured_intent: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        queries = self._normalize_queries(query, structured_intent)
        primary_query = queries[0] if queries else ""

        sparse_candidates, dense_candidates = await self.retriever.retrieve(
            queries,
            access_level=access_level,
            sparse_top_k=self.SPARSE_TOP_K,
            dense_top_k=self.DENSE_TOP_K,
        )
        fused_candidates = self.fusion.fuse(
            sparse_candidates,
            dense_candidates,
            top_k=top_k,
        )
        hits = await self.hydrator.hydrate(fused_candidates)

        self.last_debug = {
            "query": primary_query,
            "queries": queries,
            "structured_intent": structured_intent or {},
            "access_level": access_level,
            "sparse_candidates": len(sparse_candidates),
            "dense_candidates": len(dense_candidates),
            "fused_candidates": len(fused_candidates),
            "returned_candidates": len(hits),
            "sparse_top_k": self.SPARSE_TOP_K,
            "dense_top_k": self.DENSE_TOP_K,
            "fusion": "rrf",
            "retrieval_debug": self.retriever.last_debug,
            "hydration_debug": self.hydrator.last_debug,
            "candidates": [self._debug_hit(hit) for hit in hits],
        }
        logger.info(
            "RAG hybrid retrieval query={} sparse={} dense={} fused={} returned={}",
            primary_query,
            len(sparse_candidates),
            len(dense_candidates),
            len(fused_candidates),
            len(hits),
        )
        return hits

    @classmethod
    def _normalize_queries(cls, query: str | list[str], structured_intent: dict[str, Any] | None) -> list[str]:
        import re

        structured_intent = structured_intent or {}
        raw_queries = query if isinstance(query, list) else [query]
        values = []
        for raw_query in raw_queries:
            value = re.sub(r"\s+", " ", str(raw_query or "")).strip()
            if value and value not in values:
                values.append(value)
        for key in ("target",):
            value = structured_intent.get(key)
            if isinstance(value, str) and value.strip() and value.strip() not in values:
                values.append(value.strip())
        values.extend(
            str(item).strip()
            for item in structured_intent.get("intent_queries") or []
            if str(item).strip() and str(item).strip() not in values
        )
        actions = [str(item).strip() for item in structured_intent.get("actions") or [] if str(item).strip()]
        if actions:
            action_query = " ".join(actions)
            if action_query not in values:
                values.append(action_query)
        return values

    @staticmethod
    def _debug_hit(hit: SearchHit) -> dict:
        return {
            "rank": hit.rank,
            "chunk_id": hit.chunk_id,
            "document_id": hit.document_id,
            "file_name": hit.file_name,
            "page_no": hit.page_no,
            "chunk_type": hit.chunk_type,
            "score": round(hit.score, 6),
            "score_breakdown": hit.score_breakdown,
            "rank_sources": hit.rank_sources,
            "content_preview": hit.content[:240],
        }
