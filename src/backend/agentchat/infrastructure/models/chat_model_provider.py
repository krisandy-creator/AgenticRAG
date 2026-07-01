import inspect
import math
import re
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import HumanMessage
from loguru import logger

from agentchat.core.models.manager import ModelManager
from agentchat.domains.indexing.entities import SearchHit


class ChatModelProvider:
    async def stream_answer(
        self,
        question: str,
        hits: list[SearchHit],
        mode: str,
        excel_result: str | None = None,
        prompt: str | None = None,
    ) -> AsyncIterator[str]:
        if prompt:
            try:
                has_delta = False
                async for delta in self._stream_model_answer(prompt):
                    if not delta:
                        continue
                    has_delta = True
                    yield delta
                if has_delta:
                    return
            except Exception as err:
                logger.warning(f"RAG 对话模型不可用，降级为结构化兜底回答: {err}")

        answer = self._fallback_answer(question, hits, mode, excel_result)
        for part in self._chunk_answer(answer):
            yield part

    @staticmethod
    async def _stream_model_answer(prompt: str) -> AsyncIterator[str]:
        client = ModelManager.get_conversation_model()
        messages = [HumanMessage(content=prompt)]
        if hasattr(client, "astream"):
            async for chunk in client.astream(messages):
                text = ChatModelProvider._content_to_text(getattr(chunk, "content", chunk))
                if text:
                    yield text
            return

        response = client.ainvoke(messages)
        if inspect.isawaitable(response):
            response = await response
        text = ChatModelProvider._content_to_text(getattr(response, "content", response))
        if text:
            yield text

    @staticmethod
    def _fallback_answer(question: str, hits: list[SearchHit], mode: str, excel_result: str | None) -> str:
        del mode
        if not hits:
            return "未检索到权限范围内的可用资料，暂时无法基于企业知识库回答这个问题。"

        fact = ChatModelProvider._select_fact(question, hits)
        answer = ChatModelProvider._format_fact(fact) if fact else ChatModelProvider._compact_text(hits[0].content)

        if excel_result:
            answer += f"\n\n补充计算结果：{ChatModelProvider._compact_text(excel_result, limit=180)}"
        return answer

    @staticmethod
    def _select_fact(question: str, hits: list[SearchHit]) -> dict[str, Any] | None:
        question_terms = ChatModelProvider._terms(question)
        candidates: list[tuple[float, dict[str, Any]]] = []
        for position, hit in enumerate(hits):
            fact = ChatModelProvider._fact_from_hit(hit)
            if not fact:
                continue
            relevance = ChatModelProvider._fact_relevance(question_terms, fact)
            tie_breaker = 1 / (position + 1) * 0.000001
            candidates.append((relevance + tie_breaker, fact))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    @staticmethod
    def _fact_from_hit(hit: SearchHit) -> dict[str, Any] | None:
        structured = hit.metadata.get("structured") or hit.metadata.get("structured_fields") or {}
        if not isinstance(structured, dict):
            return None

        raw = structured.get("raw")
        raw_fact = raw if isinstance(raw, dict) else {}
        fact = {
            "subject": structured.get("subject") or raw_fact.get("subject"),
            "attribute": structured.get("attribute") or raw_fact.get("attribute"),
            "value": structured.get("value") or raw_fact.get("value"),
            "unit": structured.get("unit") or raw_fact.get("unit"),
            "min": structured.get("min") or raw_fact.get("min"),
            "max": structured.get("max") or raw_fact.get("max"),
            "evidence": structured.get("evidence") or raw_fact.get("evidence"),
            "title": structured.get("title") or hit.metadata.get("title"),
            "content": hit.content,
        }
        if any(fact.get(key) for key in ("subject", "attribute", "value", "min", "max")):
            return {key: value for key, value in fact.items() if value not in (None, "")}
        return None

    @staticmethod
    def _format_fact(fact: dict[str, Any]) -> str:
        subject = str(fact.get("subject") or fact.get("attribute") or "该字段").strip()
        value = ChatModelProvider._fact_value(fact)
        unit = str(fact.get("unit") or "").strip()
        evidence = str(fact.get("evidence") or "").strip()

        unit_suffix = f" {unit}" if unit and unit not in value else ""
        answer = f"{subject}是 {value}{unit_suffix}。"
        if evidence:
            answer += f"依据：{evidence}。"
        return answer

    @staticmethod
    def _fact_value(fact: dict[str, Any]) -> str:
        value = str(fact.get("value") or "").strip()
        if value:
            return value
        min_value = str(fact.get("min") or "").strip()
        max_value = str(fact.get("max") or "").strip()
        if min_value and max_value and min_value != max_value:
            return f"{min_value}~{max_value}"
        return min_value or max_value or "未提供明确字段值"

    @staticmethod
    def _fact_search_text(fact: dict[str, Any]) -> str:
        values = [
            str(value)
            for key, value in fact.items()
            if value not in (None, "")
        ]
        return " ".join(values)

    @staticmethod
    def _fact_relevance(question_terms: set[str], fact: dict[str, Any]) -> float:
        field_weights = {
            "attribute": 1.4,
            "subject": 1.2,
            "evidence": 0.8,
            "title": 0.5,
            "content": 0.2,
        }
        aggregate_score = ChatModelProvider._overlap_score(
            question_terms,
            ChatModelProvider._terms(ChatModelProvider._fact_search_text(fact)),
        )
        field_scores = [
            weight
            * ChatModelProvider._overlap_score(
                question_terms,
                ChatModelProvider._terms(str(fact.get(field) or "")),
            )
            for field, weight in field_weights.items()
        ]
        return max([aggregate_score, *field_scores])

    @staticmethod
    def _terms(text: str) -> set[str]:
        tokens = re.findall(r"[A-Za-z0-9_]{2,}", (text or "").lower())
        tokens.extend(re.findall(r"\d+(?:\.\d+)?\s*(?:mm|cm|m|毫米|厘米|米|%)?", (text or "").lower()))
        for segment in re.findall(r"[\u4e00-\u9fff]+", text or ""):
            for size in (2, 3, 4):
                tokens.extend(segment[index:index + size] for index in range(0, max(len(segment) - size + 1, 0)))
        return {token.strip() for token in tokens if token.strip()}

    @staticmethod
    def _overlap_score(left_terms: set[str], right_terms: set[str]) -> float:
        if not left_terms or not right_terms:
            return 0.0
        intersection_count = len(left_terms.intersection(right_terms))
        coverage = intersection_count / len(left_terms)
        precision = intersection_count / len(right_terms)
        return 0.75 * coverage + 0.25 * precision

    @staticmethod
    def _content_to_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            values = []
            for item in content:
                if isinstance(item, dict):
                    values.append(str(item.get("text") or item.get("content") or ""))
                else:
                    values.append(str(item))
            return "".join(values)
        return str(content or "")

    @staticmethod
    def _compact_text(text: str, limit: int = 140) -> str:
        normalized = " ".join((text or "").split())
        if not normalized:
            return "检索结果没有提供足够可读文本。"
        sentence_end_positions = [normalized.find(mark) for mark in ("。", "！", "？", ".", "!", "?")]
        sentence_end_positions = [position for position in sentence_end_positions if position > 20]
        if sentence_end_positions:
            normalized = normalized[: min(sentence_end_positions) + 1]
        if len(normalized) > limit:
            return f"{normalized[:limit].rstrip()}..."
        return normalized

    @staticmethod
    def _chunk_answer(answer: str, size: int = 36) -> list[str]:
        return [answer[index:index + size] for index in range(0, len(answer), size)]
