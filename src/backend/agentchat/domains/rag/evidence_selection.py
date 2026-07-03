import math
import re
from typing import Any

from agentchat.domains.indexing.entities import SearchHit


class EvidenceSelector:
    """在 rerank 之后做 section 聚合、意图加权与 MMR 多样性筛选。"""

    MMR_LAMBDA = 0.72
    RRF_K = 60

    # 仅文本层分块是"同一段落被窗口切开的连续片段"，可以安全合并；
    # vision_* 原子块（事实/表格行/步骤/图示/页面摘要）是解析阶段刻意拆开的
    # 自包含语义单元，合并会破坏证据粒度，一律直通。
    MERGEABLE_CHUNK_TYPES = {"pdf_text_layer", "text", "excel_markdown"}

    ANSWER_PATTERNS = {
        "quantity": r"\d+(?:\.\d+)?\s*(?:mm|cm|m|毫米|厘米|米|%|度|个|次|条|kg|g)",
        "time": r"\d+\s*(?:天|日|周|月|年|小时|分钟|秒)",
    }

    def select(
        self,
        question: str,
        hits: list[SearchHit],
        structured_intent: dict[str, Any] | None,
        top_k: int,
        *,
        rerank_provider: str = "",
    ) -> tuple[list[SearchHit], dict]:
        intent = structured_intent or {}
        if not hits:
            return [], {"provider": "evidence_selector", "input_count": 0, "selected": []}

        effective_k = self._effective_top_k(top_k, intent)
        aggregated = self._aggregate_by_section(hits)

        if rerank_provider == "configured_rerank":
            ranked = self._rank_with_tiebreak(question, aggregated, intent)
            rank_path = "rerank_authoritative"
        else:
            ranked = self._rank_by_fallback_fusion(question, aggregated, intent)
            rank_path = "fallback_rrf_fusion"

        selected = self._mmr_select(ranked, effective_k)

        for rank, hit in enumerate(selected, start=1):
            hit.rank = rank

        debug = {
            "provider": "evidence_selector",
            "rank_path": rank_path,
            "rerank_provider": rerank_provider,
            "input_count": len(hits),
            "aggregated_count": len(aggregated),
            "effective_top_k": effective_k,
            "question_type": intent.get("question_type"),
            "selected": [
                {
                    "rank": hit.rank,
                    "chunk_id": hit.chunk_id,
                    "file_name": hit.file_name,
                    "page_no": hit.page_no,
                    "score": round(hit.score, 6),
                    "score_breakdown": hit.score_breakdown,
                    "merged": bool(hit.metadata.get("merged")),
                    "merged_chunk_ids": hit.metadata.get("merged_chunk_ids", []),
                    "content_preview": hit.content[:240],
                }
                for hit in selected
            ],
        }
        return selected, debug

    @staticmethod
    def _effective_top_k(top_k: int, intent: dict[str, Any]) -> int:
        if intent.get("question_type") == "enumeration":
            return max(top_k, 8)
        return top_k

    @staticmethod
    def _select_score(hit: SearchHit) -> float:
        combined = hit.score_breakdown.get("combined_select")
        if combined is not None:
            return float(combined)
        return hit.score

    @classmethod
    def _aggregate_by_section(cls, hits: list[SearchHit]) -> list[SearchHit]:
        groups: dict[tuple[str, int | None, str], SearchHit] = {}
        passthrough: list[SearchHit] = []

        for hit in hits:
            section_title = cls._section_title(hit)
            if not section_title or hit.chunk_type not in cls.MERGEABLE_CHUNK_TYPES:
                passthrough.append(hit)
                continue

            key = (hit.document_id, hit.page_no, section_title)
            existing = groups.get(key)
            if existing is None:
                # 复制后再参与合并，避免原地篡改上游（rerank 阶段）持有的对象
                groups[key] = hit.model_copy(deep=True)
                continue

            # hits 按 rerank 排名进入，existing 是组内最优块。
            # 内容只追加、永不替换，截断只砍低排名块的尾部，
            # 保证最优证据完整保留在最前（chunk_id/score/content 保持同一性）。
            existing.content = f"{existing.content}\n{hit.content}".strip()[:4000]
            existing.score = max(existing.score, hit.score)
            merged_ids = sorted(
                {
                    *existing.metadata.get("merged_chunk_ids", []),
                    existing.chunk_id,
                    hit.chunk_id,
                }
            )
            existing.metadata = {
                **existing.metadata,
                "merged": True,
                "merged_chunk_ids": merged_ids,
            }
            existing.score_breakdown = {
                **existing.score_breakdown,
                "section_merge": round(existing.score, 6),
            }

        aggregated = [*groups.values(), *passthrough]
        aggregated.sort(key=lambda item: item.score, reverse=True)
        return aggregated

    @staticmethod
    def _section_title(hit: SearchHit) -> str:
        metadata = hit.metadata or {}
        for key in ("section_title", "title"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        structured = metadata.get("structured") or {}
        if isinstance(structured, dict):
            for key in ("section_title", "title"):
                value = structured.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    @classmethod
    def _rank_with_tiebreak(cls, question: str, hits: list[SearchHit], intent: dict[str, Any]) -> list[SearchHit]:
        ranked: list[tuple[float, SearchHit]] = []
        for hit in hits:
            intent_score = cls._intent_score(question, hit, intent)
            combined = 0.9 * hit.score + 0.1 * intent_score
            hit.score_breakdown = {
                **hit.score_breakdown,
                "intent_select": round(intent_score, 6),
                "combined_select": round(combined, 6),
            }
            ranked.append((combined, hit))

        ranked.sort(key=lambda item: item[0], reverse=True)
        return [hit for _, hit in ranked]

    @classmethod
    def _rank_by_fallback_fusion(cls, question: str, hits: list[SearchHit], intent: dict[str, Any]) -> list[SearchHit]:
        intent_scored = [(cls._intent_score(question, hit, intent), hit) for hit in hits]
        intent_rank_map = {
            hit.chunk_id: rank
            for rank, (_, hit) in enumerate(
                sorted(intent_scored, key=lambda item: item[0], reverse=True),
                start=1,
            )
        }

        rrf_rank_map = {
            hit.chunk_id: rank
            for rank, hit in enumerate(sorted(hits, key=lambda item: item.score, reverse=True), start=1)
        }

        ranked: list[tuple[float, SearchHit]] = []
        for intent_score, hit in intent_scored:
            rrf_rank = rrf_rank_map[hit.chunk_id]
            intent_rank = intent_rank_map[hit.chunk_id]
            combined = 1.0 / (cls.RRF_K + rrf_rank) + 1.0 / (cls.RRF_K + intent_rank)
            hit.score_breakdown = {
                **hit.score_breakdown,
                "intent_select": round(intent_score, 6),
                "combined_select": round(combined, 6),
                "rrf_rank": float(rrf_rank),
                "intent_rank": float(intent_rank),
            }
            ranked.append((combined, hit))

        ranked.sort(key=lambda item: item[0], reverse=True)
        return [hit for _, hit in ranked]

    @classmethod
    def _intent_score(cls, question: str, hit: SearchHit, intent: dict[str, Any]) -> float:
        target_text = cls._target_text(hit)
        if not target_text:
            return 0.0

        query_terms = cls._terms(
            " ".join(
                value
                for value in [
                    question,
                    intent.get("target") or "",
                    " ".join(intent.get("actions") or []),
                    " ".join(intent.get("intent_queries") or []),
                ]
                if value
            )
        )
        target_terms = cls._terms(target_text)
        if not query_terms or not target_terms:
            overlap = 0.0
        else:
            overlap = len(query_terms.intersection(target_terms)) / math.sqrt(len(query_terms) * len(target_terms))

        section_title = cls._section_title(hit)
        title_bonus = 0.0
        if section_title:
            title_terms = cls._terms(section_title)
            if title_terms:
                title_overlap = len(query_terms.intersection(title_terms)) / math.sqrt(
                    len(query_terms) * len(title_terms)
                )
                title_bonus = 0.35 * title_overlap

        exact_bonus = 0.0
        target = str(intent.get("target") or "").strip()
        if target and target in target_text:
            exact_bonus = 0.25
        elif section_title and target and target in section_title:
            exact_bonus = 0.35

        answerability = cls._answerability_bonus(question, intent, target_text)
        return min(overlap + title_bonus + exact_bonus + answerability, 1.0)

    @classmethod
    def _answerability_bonus(cls, question: str, intent: dict[str, Any], target_text: str) -> float:
        if intent.get("question_type") != "fact":
            return 0.0
        if re.search(r"(多少|几|多长|多久|多远|何时|什么时候)", question):
            for pattern in cls.ANSWER_PATTERNS.values():
                if re.search(pattern, target_text, re.I):
                    return 0.15
        return 0.0

    @staticmethod
    def _target_text(hit: SearchHit) -> str:
        section_title = EvidenceSelector._section_title(hit)
        parts = [section_title, hit.content]
        return "\n".join(part for part in parts if part).strip()

    @classmethod
    def _mmr_select(cls, hits: list[SearchHit], top_k: int) -> list[SearchHit]:
        if len(hits) <= top_k:
            return hits[:top_k]

        selected: list[SearchHit] = []
        remaining = hits[:]

        while remaining and len(selected) < top_k:
            best_index = 0
            best_score = float("-inf")
            for index, candidate in enumerate(remaining):
                relevance = cls._select_score(candidate)
                redundancy = 0.0
                if selected:
                    redundancy = max(cls._similarity(candidate, chosen) for chosen in selected)
                mmr_score = cls.MMR_LAMBDA * relevance - (1 - cls.MMR_LAMBDA) * redundancy
                if mmr_score > best_score:
                    best_score = mmr_score
                    best_index = index
            selected.append(remaining.pop(best_index))

        return selected

    @classmethod
    def _similarity(cls, left: SearchHit, right: SearchHit) -> float:
        left_terms = cls._terms(left.content)
        right_terms = cls._terms(right.content)
        if left.document_id == right.document_id and left.page_no == right.page_no:
            if not left_terms or not right_terms:
                return 0.5
            content_sim = len(left_terms.intersection(right_terms)) / math.sqrt(
                len(left_terms) * len(right_terms)
            )
            return max(0.5, content_sim)

        if not left_terms or not right_terms:
            return 0.0
        return len(left_terms.intersection(right_terms)) / math.sqrt(len(left_terms) * len(right_terms))

    @staticmethod
    def _terms(text: str) -> set[str]:
        tokens = re.findall(r"[A-Za-z0-9_]{2,}", text.lower())
        for segment in re.findall(r"[\u4e00-\u9fff]+", text):
            for size in (2, 3, 4):
                tokens.extend(segment[index : index + size] for index in range(0, max(len(segment) - size + 1, 0)))
        return set(token for token in tokens if token.strip())
