import inspect
import json
import re
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger

from agentchat.core.models.manager import ModelManager


@dataclass
class QueryExpansionResult:
    original_query: str
    queries: list[str] = field(default_factory=list)
    structured_intent: dict[str, Any] = field(default_factory=dict)
    provider: str = "deterministic_query_expansion"
    debug: dict[str, Any] = field(default_factory=dict)

    @property
    def primary_query(self) -> str:
        return self.queries[0] if self.queries else self.original_query


class QueryExpansionService:
    """根据用户问题生成多条可召回 query，并抽取通用结构化意图。"""

    MAX_QUERIES = 8
    MAX_PHRASES = 8

    async def expand(self, question: str, mode: str) -> QueryExpansionResult:
        deterministic = self._deterministic_result(question, mode)
        llm_result = await self._try_llm_expand(question, mode)
        if not llm_result:
            return deterministic

        queries = self._merge_queries(deterministic.queries, llm_result.queries)
        structured_intent = {
            **deterministic.structured_intent,
            **{key: value for key, value in llm_result.structured_intent.items() if value},
        }
        return QueryExpansionResult(
            original_query=question,
            queries=queries,
            structured_intent=structured_intent,
            provider=llm_result.provider,
            debug={
                "deterministic": deterministic.debug,
                "llm": llm_result.debug,
            },
        )

    async def _try_llm_expand(self, question: str, mode: str) -> QueryExpansionResult | None:
        try:
            client = ModelManager.get_conversation_model()
            messages = [
                SystemMessage(content=self._system_prompt()),
                HumanMessage(content=json.dumps({"question": question, "mode": mode}, ensure_ascii=False)),
            ]
            response = client.ainvoke(messages)
            if inspect.isawaitable(response):
                response = await response
            text = self._content_to_text(getattr(response, "content", response))
            payload = self._extract_json(text)
            queries = self._clean_queries(payload.get("queries") or [], question)
            structured_intent = self._normalize_structured_intent(payload.get("structured_intent") or {})
            if not queries:
                return None
            return QueryExpansionResult(
                original_query=question,
                queries=queries,
                structured_intent=structured_intent,
                provider="llm_query_expansion",
                debug={"raw_text": text[:1200]},
            )
        except Exception as err:
            logger.warning(f"查询扩展模型不可用，降级为确定性扩展: {err}")
            return None

    @staticmethod
    def _system_prompt() -> str:
        return (
            "你是企业知识库 RAG 的查询扩展器。请只输出严格 JSON，不要 Markdown。\n"
            "目标：把用户问题改写为 4 到 8 条适合混合检索的 query，并抽取结构化意图。\n"
            "要求：\n"
            "1. 保留用户问题里的数字、单位、专有名词、缩写和约束条件。\n"
            "2. 可以补充自然语言中的等价表达，但不要编造答案、数值或源文档不存在的事实。\n"
            "3. query 要覆盖：原始问法、关键词问法、实体/动作/约束问法、适合表格或事实字段检索的问法。\n"
            "4. structured_intent 只放通用字段：entities、actions、constraints、quantities、time_conditions、target、intent_queries、question_type。\n"
            "5. intent_queries 应聚焦答案意图，不要重复堆叠项目实体名；question_type 取 fact 或 enumeration。\n"
            "JSON 结构：\n"
            "{"
            "\"queries\": [\"\"], "
            "\"structured_intent\": {"
            "\"entities\": [], \"actions\": [], \"constraints\": [], "
            "\"quantities\": [], \"time_conditions\": [], \"target\": \"\", "
            "\"intent_queries\": [], \"question_type\": \"fact\""
            "}"
            "}"
        )

    def _deterministic_result(self, question: str, mode: str) -> QueryExpansionResult:
        normalized = self._normalize_text(question)
        keyword_query = self._keyword_query(normalized)
        quantities = self._extract_quantities(question)
        entities = self._extract_entities(normalized)
        constraints = self._extract_constraints(question)
        actions = self._extract_actions(normalized)
        target = self._extract_target(normalized, question)
        intent_queries = self._build_intent_queries(question, normalized, target, actions)
        question_type = self._detect_question_type(question)

        queries = [question, normalized]
        if target:
            queries.append(target)
        queries.extend(intent_queries)
        if entities and len(entities) <= 4:
            queries.append(" ".join(entities))
        if quantities:
            queries.append(" ".join([*(entities[:3]), *quantities]))
        if mode == "deep_analysis":
            queries.append(normalized)

        structured_intent = {
            "entities": entities,
            "actions": actions,
            "constraints": constraints,
            "quantities": quantities,
            "time_conditions": self._extract_time_conditions(question),
            "target": target or keyword_query,
            "intent_queries": intent_queries,
            "question_type": question_type,
        }
        return QueryExpansionResult(
            original_query=question,
            queries=self._clean_queries(queries, question),
            structured_intent=structured_intent,
            provider="deterministic_query_expansion",
            debug={
                "normalized": normalized,
                "keyword_query": keyword_query,
                "quantities": quantities,
                "entities": entities,
                "constraints": constraints,
                "actions": actions,
                "target": target,
                "intent_queries": intent_queries,
                "question_type": question_type,
            },
        )

    @classmethod
    def _keyword_query(cls, text: str) -> str:
        return " ".join(cls._extract_entities(text)).strip() or text

    @staticmethod
    def _normalize_text(text: str) -> str:
        normalized = re.sub(r"\s+", " ", text or "").strip()
        normalized = re.sub(r"[？?。！!，,；;：:]+", " ", normalized)
        normalized = re.sub(r"(\d+(?:\.\d+)?)(mm|cm|m|毫米|厘米|米)", r"\1 \2", normalized, flags=re.I)
        return re.sub(r"\s+", " ", normalized).strip()

    @staticmethod
    def _extract_quantities(text: str) -> list[str]:
        matches = re.findall(r"\d+(?:\.\d+)?\s*(?:mm|cm|m|毫米|厘米|米|%|度)?", text or "", re.I)
        return list(dict.fromkeys(match.strip() for match in matches if match.strip()))

    @classmethod
    def _extract_entities(cls, text: str) -> list[str]:
        scoped = cls._entity_scope(text)
        values = []
        values.extend(re.findall(r"[A-Za-z][A-Za-z0-9_+-]{1,}", scoped or ""))
        for segment in re.findall(r"[\u4e00-\u9fff]+", scoped or ""):
            values.extend(cls._split_chinese_segment(segment))
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))[: cls.MAX_PHRASES]

    @classmethod
    def _entity_scope(cls, text: str) -> str:
        cleaned = cls._normalize_text(text)
        parts = re.split(r"(有哪些|包括哪些|包含哪些|是什么|什么是|如何|怎么)", cleaned, maxsplit=1)
        return parts[0].strip() if parts else cleaned

    @staticmethod
    def _extract_constraints(text: str) -> list[str]:
        values = re.findall(r"[^，,。；;？?\s]*[<>≤≥=][^，,。；;？?\s]*", text or "")
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))[:8]

    @staticmethod
    def _split_chinese_segment(segment: str) -> list[str]:
        if len(segment) <= 8:
            return [segment]
        if len(segment) > 12:
            return [segment]
        values = []
        for size in (4, 6):
            values.extend(segment[index : index + size] for index in range(0, len(segment) - size + 1, size))
        return values or [segment]

    @classmethod
    def _extract_actions(cls, text: str) -> list[str]:
        patterns = [
            r"(有哪些|包括哪些|包含哪些|列举|列出|什么是|是什么|如何|怎么|多少|是否)",
        ]
        values = []
        for pattern in patterns:
            values.extend(re.findall(pattern, text or ""))
        stripped = cls._strip_question_noise(text)
        if stripped and stripped != text:
            values.append(stripped)
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))[: cls.MAX_PHRASES]

    @classmethod
    def _extract_target(cls, normalized: str, original: str) -> str:
        source = normalized or original or ""
        patterns = [
            r"(?:有哪些|包括哪些|包含哪些|什么是|是什么|何谓)(.+)$",
            r"(?:如何|怎么)(.+)$",
            r"(.+?)(?:有哪些|包括哪些|包含哪些)[？?]?$",
        ]
        for pattern in patterns:
            match = re.search(pattern, source)
            if not match:
                continue
            target = cls._strip_question_noise(match.group(1))
            if target:
                return target
        return cls._strip_question_noise(source)

    @classmethod
    def _build_intent_queries(
        cls,
        question: str,
        normalized: str,
        target: str,
        actions: list[str],
    ) -> list[str]:
        values = []
        if target:
            values.append(target)
            if actions:
                values.append(f"{' '.join(actions[:2])} {target}".strip())
        stripped = cls._strip_question_noise(normalized or question)
        if stripped and stripped not in values:
            values.append(stripped)
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))[:4]

    @staticmethod
    def _strip_question_noise(text: str) -> str:
        cleaned = re.sub(r"\s+", " ", text or "").strip()
        cleaned = re.sub(r"^(请问|请说明|请介绍|帮我|我想知道)\s*", "", cleaned)
        cleaned = re.sub(r"(有哪些|包括哪些|包含哪些|是什么|什么是|如何|怎么|吗|呢)[？?]*$", "", cleaned).strip()
        cleaned = re.sub(r"^[？?]+|[？?]+$", "", cleaned).strip()
        return cleaned

    @staticmethod
    def _detect_question_type(question: str) -> str:
        if re.search(r"(有哪些|包括哪些|包含哪些|列举|列出|清单|标准有哪些|要求有哪些)", question or ""):
            return "enumeration"
        return "fact"

    @staticmethod
    def _extract_time_conditions(text: str) -> list[str]:
        matches = re.findall(r"\d+\s*(?:天|日|周|月|年|小时|分钟)", text or "")
        return list(dict.fromkeys(match.strip() for match in matches if match.strip()))[:8]

    @classmethod
    def _clean_queries(cls, queries: list[Any], original_query: str) -> list[str]:
        values = [original_query, *[str(query) for query in queries if query]]
        cleaned = []
        for query in values:
            value = re.sub(r"\s+", " ", query).strip()
            if not value or value in cleaned:
                continue
            cleaned.append(value)
            if len(cleaned) >= cls.MAX_QUERIES:
                break
        return cleaned

    @classmethod
    def _merge_queries(cls, first: list[str], second: list[str]) -> list[str]:
        return cls._clean_queries([*first, *second], first[0] if first else "")

    @staticmethod
    def _normalize_structured_intent(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        allowed = {
            "entities",
            "actions",
            "constraints",
            "quantities",
            "time_conditions",
            "target",
            "intent_queries",
            "question_type",
        }
        return {key: value.get(key) for key in allowed if value.get(key)}

    @staticmethod
    def _extract_json(raw_text: str) -> dict:
        text = raw_text.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        start = text.find("{")
        if start < 0:
            return {}
        try:
            payload, _ = json.JSONDecoder().raw_decode(text[start:])
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _content_to_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            values = []
            for item in content:
                if isinstance(item, dict):
                    values.append(str(item.get("text") or item.get("content") or item))
                else:
                    values.append(str(item))
            return "\n".join(values)
        return str(content)
