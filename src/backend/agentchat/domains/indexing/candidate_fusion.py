from __future__ import annotations

from agentchat.domains.indexing.entities import RetrievalCandidate


class CandidateFusionService:
    """RRF 融合 ES 与 Milvus 召回结果。"""

    DEFAULT_RRF_K = 60

    def fuse(
        self,
        sparse_candidates: list[RetrievalCandidate],
        dense_candidates: list[RetrievalCandidate],
        top_k: int,
        *,
        rrf_k: int = DEFAULT_RRF_K,
    ) -> list[RetrievalCandidate]:
        merged: dict[str, RetrievalCandidate] = {}

        for candidate in sparse_candidates:
            merged[candidate.chunk_id] = RetrievalCandidate(
                chunk_id=candidate.chunk_id,
                document_id=candidate.document_id,
                vector_id=candidate.vector_id,
                sparse_score=candidate.sparse_score,
                sparse_rank=candidate.sparse_rank,
                dense_score=candidate.dense_score,
                dense_rank=candidate.dense_rank,
                content=candidate.content,
            )

        for candidate in dense_candidates:
            existing = merged.get(candidate.chunk_id)
            if existing is None:
                merged[candidate.chunk_id] = RetrievalCandidate(
                    chunk_id=candidate.chunk_id,
                    document_id=candidate.document_id,
                    vector_id=candidate.vector_id,
                    dense_score=candidate.dense_score,
                    dense_rank=candidate.dense_rank,
                )
                continue
            existing.vector_id = candidate.vector_id or existing.vector_id
            existing.dense_score = max(existing.dense_score, candidate.dense_score)
            if candidate.dense_rank:
                existing.dense_rank = (
                    candidate.dense_rank
                    if not existing.dense_rank
                    else min(existing.dense_rank, candidate.dense_rank)
                )

        fused: list[RetrievalCandidate] = []
        for candidate in merged.values():
            candidate.rrf_score = self._rrf_score(candidate, rrf_k=rrf_k)
            fused.append(candidate)

        fused.sort(key=lambda item: (-item.rrf_score, item.sparse_rank or 10**9, item.dense_rank or 10**9))
        selected = fused[:top_k]
        for rank, candidate in enumerate(selected, start=1):
            candidate.fusion_rank = rank
        return selected

    @staticmethod
    def _rrf_score(candidate: RetrievalCandidate, *, rrf_k: int) -> float:
        score = 0.0
        if candidate.sparse_rank:
            score += 1.0 / (rrf_k + candidate.sparse_rank)
        if candidate.dense_rank:
            score += 1.0 / (rrf_k + candidate.dense_rank)
        return score
