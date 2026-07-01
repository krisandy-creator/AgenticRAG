from agentchat.domains.indexing.entities import SearchHit


class RerankProvider:
    def __init__(self):
        self.last_provider = "fallback_overlap"
        self.last_debug: dict = {}

    async def rerank_hits(self, query: str, hits: list[SearchHit], top_k: int) -> list[SearchHit]:
        if not hits:
            self.last_debug = {"provider": self.last_provider, "input_count": 0, "selected": []}
            return []

        try:
            from agentchat.services.rag.rerank import Reranker

            results = await Reranker.rerank_documents(query, [hit.content for hit in hits])
            reranked: list[SearchHit] = []
            for result in results[:top_k]:
                hit = hits[result.index]
                hit.score = float(result.score)
                hit.score_breakdown = {
                    **hit.score_breakdown,
                    "rerank": round(float(result.score), 6),
                }
                reranked.append(hit)
            if reranked:
                self.last_provider = "configured_rerank"
                self.last_debug = {
                    "provider": self.last_provider,
                    "input_count": len(hits),
                    "selected": [self._debug_hit(hit) for hit in reranked],
                }
                return reranked
        except Exception as err:
            self.last_debug = {
                "provider": "configured_rerank_failed",
                "input_count": len(hits),
                "error": str(err),
            }

        self.last_provider = "fallback_overlap"
        reranked = sorted(hits, key=lambda item: item.score, reverse=True)[:top_k]
        self.last_debug = {
            "provider": self.last_provider,
            "input_count": len(hits),
            "selected": [self._debug_hit(hit) for hit in reranked],
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
