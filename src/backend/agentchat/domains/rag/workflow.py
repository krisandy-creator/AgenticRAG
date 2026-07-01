import json
from collections.abc import AsyncIterator

from agentchat.domains.identity.entities import EnterpriseUser
from agentchat.domains.indexing.entities import SearchHit
from agentchat.domains.indexing.services import IndexingService
from agentchat.domains.observability.event_schema import TraceEvent
from agentchat.domains.observability.services import TraceService
from agentchat.domains.rag.excel_tool import ExcelAnalysisTool
from agentchat.domains.rag.prompts import build_rag_prompt
from agentchat.domains.rag.query_expansion import QueryExpansionService
from agentchat.domains.rag.repositories import ChatRepository
from agentchat.infrastructure.models.chat_model_provider import ChatModelProvider
from agentchat.infrastructure.models.rerank_provider import RerankProvider


class RagWorkflow:
    def __init__(self):
        self.indexing = IndexingService()
        self.reranker = RerankProvider()
        self.chat_model = ChatModelProvider()
        self.excel_tool = ExcelAnalysisTool()
        self.query_expansion = QueryExpansionService()

    async def stream(self, session_id: str, user: EnterpriseUser, question: str, mode: str) -> AsyncIterator[TraceEvent]:
        trace = TraceService.create_trace(session_id=session_id, user_id=user.user_id, question=question, mode=mode)
        seq = 1
        answer_parts: list[str] = []
        reranked_hits: list[SearchHit] = []

        def event(event_type: str, payload: dict) -> TraceEvent:
            nonlocal seq
            emitted = TraceService.emit(trace.trace_id, seq, event_type, payload)
            seq += 1
            return emitted

        try:
            ChatRepository.add_message(session_id=session_id, role="user", content=question)
            yield event("trace_started", {"session_id": session_id, "user_id": user.user_id})
            yield event("mode_selected", {"mode": mode})

            yield event("query_rewrite_started", {"question": question})
            expansion = await self.query_expansion.expand(question, mode)
            yield event(
                "query_rewrite_finished",
                {
                    "rewritten_query": expansion.primary_query,
                    "expanded_queries": expansion.queries,
                    "structured_intent": expansion.structured_intent,
                    "provider": expansion.provider,
                    "debug": expansion.debug,
                },
            )

            candidate_k = 50 if mode == "deep_analysis" else 40
            rerank_top_k = 8 if mode == "deep_analysis" else 6
            yield event(
                "retrieval_started",
                {
                    "query": expansion.primary_query,
                    "queries": expansion.queries,
                    "candidate_k": candidate_k,
                    "rerank_top_k": rerank_top_k,
                    "access_level": user.access_level,
                },
            )
            hits = await self.indexing.search(
                expansion.queries,
                access_level=user.access_level,
                top_k=candidate_k,
                structured_intent=expansion.structured_intent,
            )
            yield event("retrieval_debug", self.indexing.last_debug)
            for hit in hits:
                yield event("retrieval_hit", self._hit_payload(hit))
            yield event("retrieval_finished", {"hit_count": len(hits)})

            yield event("rerank_started", {"candidate_count": len(hits)})
            reranked_hits = await self.reranker.rerank_hits(question, hits, top_k=rerank_top_k)
            yield event(
                "rerank_finished",
                {
                    "hit_count": len(reranked_hits),
                    "hits": [self._hit_payload(hit) for hit in reranked_hits],
                    "provider": self.reranker.last_provider,
                    "debug": self.reranker.last_debug,
                },
            )

            excel_result = None
            file_types = list({hit.file_type for hit in reranked_hits})
            if mode == "deep_analysis" and self.excel_tool.should_run(question, file_types):
                document_ids = list({hit.document_id for hit in reranked_hits})
                yield event("excel_analysis_started", {"document_ids": document_ids})
                excel_result = await self.excel_tool.run(question, document_ids)
                yield event("excel_analysis_finished", {"has_result": bool(excel_result), "preview": (excel_result or "")[:240]})

            prompt = build_rag_prompt(question, reranked_hits, mode, excel_result)
            yield event(
                "prompt_built",
                {
                    "evidence_count": len(reranked_hits),
                    "prompt_length": len(prompt),
                    "has_excel_result": bool(excel_result),
                    "instruction": "已要求模型筛选关键证据，只输出结论，不在正文粘贴原文片段。",
                },
            )

            async for delta in self.chat_model.stream_answer(question, reranked_hits, mode, excel_result, prompt):
                answer_parts.append(delta)
                yield event("answer_delta", {"delta": delta})

            citations = self._citations(reranked_hits)
            for citation in citations:
                yield event("citation", citation)

            answer = "".join(answer_parts).strip()
            ChatRepository.add_message(session_id=session_id, role="assistant", content=answer, trace_id=trace.trace_id)
            TraceService.finish(trace.trace_id, "success")
            yield event("trace_finished", {"status": "success", "citation_count": len(citations)})
        except Exception as err:
            yield event("error", {"message": str(err)})
            TraceService.finish(trace.trace_id, "failed")

    @staticmethod
    def _hit_payload(hit: SearchHit) -> dict:
        return {
            "chunk_id": hit.chunk_id,
            "document_id": hit.document_id,
            "file_name": hit.file_name,
            "file_type": hit.file_type,
            "page_no": hit.page_no,
            "chunk_type": hit.chunk_type,
            "score": round(hit.score, 4),
            "rank": hit.rank,
            "snippet": hit.content[:320],
            "content": hit.content,
            "content_length": len(hit.content),
            "metadata": hit.metadata,
            "score_breakdown": hit.score_breakdown,
            "rank_sources": hit.rank_sources,
        }

    @staticmethod
    def _citations(hits: list[SearchHit]) -> list[dict]:
        citations = []
        seen = set()
        for hit in hits:
            key = (hit.document_id, hit.page_no, hit.chunk_id)
            if key in seen:
                continue
            seen.add(key)
            page_label = f"第 {hit.page_no} 页" if hit.page_no else "文档片段"
            citations.append(
                {
                    "document_id": hit.document_id,
                    "file_name": hit.file_name,
                    "page_no": hit.page_no,
                    "chunk_id": hit.chunk_id,
                    "source_label": f"{hit.file_name} · {page_label}",
                }
            )
        return citations


def sse_format(event: TraceEvent) -> str:
    return f"event: {event.event_type}\ndata: {json.dumps(event.model_dump(), ensure_ascii=False)}\n\n"
