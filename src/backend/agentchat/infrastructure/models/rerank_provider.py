from typing import Any

from agentchat.domains.indexing.entities import SearchHit


class RerankProvider:
    def __init__(self):
        self.last_provider = "fallback_rrf"
        self.last_debug: dict = {}

    async def rerank_hits(
        self,
        query: str,
        hits: list[SearchHit],
        top_k: int,
        structured_intent: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        if not hits:
            self.last_debug = {"provider": self.last_provider, "input_count": 0, "selected": []}
            return []

        rerank_failure: dict[str, Any] | None = None

        try:
            from agentchat.services.rag.rerank import Reranker

            results = await Reranker.rerank_documents(
                query,
                [hit.content for hit in hits],
                top_n=len(hits),
            )
            reranked: list[SearchHit] = []
            for result in results:
                hit = hits[result.index]
                hit.score = float(result.score)
                hit.score_breakdown = {
                    **hit.score_breakdown,
                    "rerank": round(float(result.score), 6),
                }
                reranked.append(hit)
            reranked.sort(key=lambda item: item.score, reverse=True)
            if reranked:
                self.last_provider = "configured_rerank"
                self.last_debug = {
                    "provider": self.last_provider,
                    "input_count": len(hits),
                    "returned_count": len(reranked),
                    "selected_preview": [self._debug_hit(hit) for hit in reranked[:top_k]],
                }
                return reranked
            rerank_failure = {
                "provider": "configured_rerank_empty",
                "input_count": len(hits),
                "reason": "rerank API 返回空结果",
            }
        except Exception as err:
            rerank_failure = {
                "provider": "configured_rerank_failed",
                "input_count": len(hits),
                "error": str(err),
            }

        self.last_provider = "fallback_rrf"
        reranked = sorted(hits, key=lambda item: item.score, reverse=True)
        self.last_debug = {
            "provider": self.last_provider,
            "input_count": len(hits),
            "rerank_failure": rerank_failure,
            "returned_count": len(reranked),
            "selected_preview": [self._debug_hit(hit) for hit in reranked[:top_k]],
        }
        return reranked

    @staticmethod
    def _debug_hit(hit: SearchHit) -> dict:
        return {
            "rank": hit.rank,
            "chunk_id": hit.chunk_id,
            "file_name": hit.file_name,
            "page_no": hit.page_no,
            "chunk_type": hit.chunk_type,
            "score": round(hit.score, 6),
            "score_breakdown": hit.score_breakdown,
            "content_preview": hit.content[:240],
        }
