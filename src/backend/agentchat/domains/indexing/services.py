import json
import math
import re
from collections import Counter
from typing import Any

from loguru import logger

from agentchat.domains.indexing.chunking import TextChunk
from agentchat.domains.indexing.entities import DocumentChunkTable, SearchHit
from agentchat.domains.indexing.repositories import ChunkRepository
from agentchat.infrastructure.models.embedding_provider import EmbeddingProvider
from agentchat.infrastructure.vector_store.milvus_store import MilvusVectorStore


class IndexingService:
    VECTOR_WEIGHT = 0.55
    KEYWORD_WEIGHT = 0.35
    STRUCTURED_WEIGHT = 0.10
    INITIAL_SCAN_MULTIPLIER = 80
    VECTOR_STORE_CANDIDATE_MULTIPLIER = 2

    def __init__(self):
        self.embedding_provider = EmbeddingProvider()
        self.vector_store = MilvusVectorStore()
        self.last_debug: dict = {}

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
        if not chunks:
            if replace and start_index == 0:
                ChunkRepository.replace_chunks(document.document_id, [])
            return []

        db_chunks: list[DocumentChunkTable] = []
        all_embeddings: list[list[float]] = []

        for batch_start in range(0, len(chunks), embedding_batch_size):
            batch = chunks[batch_start: batch_start + embedding_batch_size]
            embeddings = await self.embedding_provider.embed_texts_batched(
                [chunk.content for chunk in batch],
                api_batch_size=embedding_api_batch_size,
                max_concurrency=embedding_max_concurrency,
            )
            all_embeddings.extend(embeddings)
            for offset, chunk in enumerate(batch):
                global_index = start_index + batch_start + offset
                embedding = embeddings[offset] if offset < len(embeddings) else []
                metadata = {
                    **chunk.metadata,
                    "file_name": document.file_name,
                    "file_type": document.file_type,
                    "embedding_dim": len(embedding),
                }
                db_chunks.append(
                    DocumentChunkTable(
                        document_id=document.document_id,
                        page_no=chunk.page_no,
                        chunk_type=chunk.chunk_type,
                        content=chunk.content,
                        vector_id=f"{document.document_id}_{global_index}",
                        permission_level=document.permission_level,
                        metadata_json=json.dumps(metadata, ensure_ascii=False),
                    )
                )

        if replace and start_index == 0:
            ChunkRepository.replace_chunks(document.document_id, db_chunks)
            await self.vector_store.upsert_chunks(db_chunks, all_embeddings)
        else:
            ChunkRepository.append_chunks(document.document_id, db_chunks)
            await self.vector_store.upsert_chunks_incremental(db_chunks, all_embeddings)
        return db_chunks

    async def search(
        self,
        query: str | list[str],
        access_level: int,
        top_k: int = 40,
        structured_intent: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        queries = self._normalize_queries(query)
        primary_query = queries[0] if queries else ""
        combined_query = " ".join(queries)
        scan_limit = max(top_k * self.INITIAL_SCAN_MULTIPLIER, 1000)
        vector_top_k = max(top_k * self.VECTOR_STORE_CANDIDATE_MULTIPLIER, 50)
        vector_candidate_scores, vector_provider, vector_debug = await self._vector_store_scores(
            queries,
            access_level=access_level,
            top_k=vector_top_k,
        )
        keyword_rows = ChunkRepository.list_searchable_chunks(access_level=access_level, limit=scan_limit)
        vector_rows = ChunkRepository.list_searchable_chunks_by_vector_ids(
            list(vector_candidate_scores.keys()),
            access_level=access_level,
        )
        rows = self._merge_rows(keyword_rows, vector_rows)
        records = []

        for chunk, document in rows:
            metadata = self._load_metadata(chunk.metadata_json)
            records.append((chunk, document, metadata))

        searchable_contents = [self._searchable_text(chunk.content, metadata) for chunk, _, metadata in records]
        keyword_scores = self._bm25_multi_scores(queries, searchable_contents)
        if vector_candidate_scores:
            vector_scores = [vector_candidate_scores.get(chunk.vector_id, 0.0) for chunk, _, _ in records]
        else:
            vector_scores, vector_provider = await self._local_vector_scores(queries, searchable_contents)
        keyword_norm = self._normalize_scores(keyword_scores)
        vector_norm = self._normalize_scores(vector_scores)

        hits: list[SearchHit] = []
        for index, (chunk, document, metadata) in enumerate(records):
            structured_score = self._structured_score(queries, structured_intent or {}, chunk.content, metadata)
            score = (
                self.VECTOR_WEIGHT * vector_norm[index]
                + self.KEYWORD_WEIGHT * keyword_norm[index]
                + self.STRUCTURED_WEIGHT * structured_score
            )
            if score <= 0:
                continue

            score_breakdown = {
                "vector": round(vector_norm[index], 6),
                "keyword": round(keyword_norm[index], 6),
                "structured": round(structured_score, 6),
                "intent": round(structured_score, 6),
                "raw_vector": round(vector_scores[index], 6),
                "raw_keyword": round(keyword_scores[index], 6),
            }
            hits.append(
                SearchHit(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    file_name=document.file_name,
                    file_type=document.file_type,
                    content=chunk.content,
                    page_no=chunk.page_no,
                    chunk_type=chunk.chunk_type,
                    permission_level=chunk.permission_level,
                    score=score,
                    metadata=self._public_metadata(metadata),
                    score_breakdown=score_breakdown,
                    rank_sources=self._rank_sources(score_breakdown),
                )
            )

        hits.sort(key=lambda item: item.score, reverse=True)
        selected_hits = hits[:top_k]
        for rank, hit in enumerate(selected_hits, start=1):
            hit.rank = rank

        self.last_debug = {
            "query": primary_query,
            "queries": queries,
            "combined_query": combined_query,
            "structured_intent": structured_intent or {},
            "access_level": access_level,
            "scanned_chunks": len(records),
            "scan_limit": scan_limit,
            "keyword_candidates": len(keyword_rows),
            "vector_candidates": len(vector_candidate_scores),
            "vector_top_k": vector_top_k,
            "returned_candidates": len(selected_hits),
            "vector_provider": vector_provider,
            "vector_debug": vector_debug,
            "score_weights": {
                "vector": self.VECTOR_WEIGHT,
                "keyword": self.KEYWORD_WEIGHT,
                "structured": self.STRUCTURED_WEIGHT,
            },
            "candidates": [self._debug_hit(hit) for hit in selected_hits],
        }
        logger.info(
            "RAG hybrid retrieval query={} queries={} scanned={} returned={} vector_provider={}",
            primary_query,
            len(queries),
            len(records),
            len(selected_hits),
            vector_provider,
        )
        return selected_hits

    @staticmethod
    def _lexical_score(query: str, content: str) -> float:
        query_terms = IndexingService._terms(query)
        content_terms = IndexingService._terms(content)
        if not query_terms or not content_terms:
            return 0.0
        overlap = query_terms.intersection(content_terms)
        exact_bonus = 1.0 if query.strip() and query.strip() in content else 0.0
        return len(overlap) / math.sqrt(len(query_terms) * len(content_terms)) + exact_bonus

    @staticmethod
    def _terms(text: str) -> set[str]:
        return set(IndexingService._term_list(text))

    @staticmethod
    def _term_list(text: str) -> list[str]:
        tokens = re.findall(r"[A-Za-z0-9_]{2,}", text.lower())
        tokens.extend(re.findall(r"\d+(?:\.\d+)?\s*(?:mm|cm|m|毫米|厘米)?", text.lower()))
        for segment in re.findall(r"[\u4e00-\u9fff]+", text):
            for size in (2, 3, 4):
                tokens.extend(segment[index:index + size] for index in range(0, max(len(segment) - size + 1, 0)))
        return [token for token in tokens if token.strip()]

    @classmethod
    def _normalize_queries(cls, query: str | list[str]) -> list[str]:
        raw_queries = query if isinstance(query, list) else [query]
        values = []
        for raw_query in raw_queries:
            value = re.sub(r"\s+", " ", str(raw_query or "")).strip()
            if value and value not in values:
                values.append(value)
        return values

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

    @classmethod
    def _searchable_text(cls, content: str, metadata: dict) -> str:
        metadata_text = cls._flatten_text(cls._public_metadata(metadata))
        return "\n".join(value for value in (content, metadata_text) if value).strip()

    @classmethod
    def _bm25_scores(cls, query: str, contents: list[str]) -> list[float]:
        query_terms = cls._term_list(query)
        if not query_terms or not contents:
            return [0.0 for _ in contents]

        doc_term_counts = [Counter(cls._term_list(content)) for content in contents]
        doc_lengths = [sum(counter.values()) or 1 for counter in doc_term_counts]
        avg_doc_length = sum(doc_lengths) / len(doc_lengths) if doc_lengths else 1.0
        doc_freq = Counter()
        for counter in doc_term_counts:
            for term in set(counter):
                doc_freq[term] += 1

        total_docs = len(contents)
        k1 = 1.5
        b = 0.75
        scores = []
        for counter, doc_length in zip(doc_term_counts, doc_lengths):
            score = 0.0
            for term in set(query_terms):
                term_freq = counter.get(term, 0)
                if term_freq <= 0:
                    continue
                idf = math.log(1 + (total_docs - doc_freq[term] + 0.5) / (doc_freq[term] + 0.5))
                denom = term_freq + k1 * (1 - b + b * doc_length / avg_doc_length)
                score += idf * (term_freq * (k1 + 1)) / denom
            scores.append(score)
        return scores

    @classmethod
    def _bm25_multi_scores(cls, queries: list[str], contents: list[str]) -> list[float]:
        if not queries or not contents:
            return [0.0 for _ in contents]

        all_scores = [cls._bm25_scores(query, contents) for query in queries if query]
        if not all_scores:
            return [0.0 for _ in contents]
        return [max(scores[index] for scores in all_scores) for index in range(len(contents))]

    async def _vector_store_scores(
        self,
        queries: list[str],
        access_level: int,
        top_k: int,
    ) -> tuple[dict[str, float], str, dict]:
        if not queries:
            return {}, self.vector_store.last_provider, {}

        embeddings = await self.embedding_provider.embed_texts(queries)
        embedding_provider = self.embedding_provider.last_provider
        if len(embeddings) != len(queries):
            return {}, embedding_provider, {"reason": "query_embedding_count_mismatch"}

        scores: dict[str, float] = {}
        for query_vector in embeddings:
            hits = await self.vector_store.search(query_vector, filters={"access_level": access_level}, top_k=top_k)
            for hit in hits:
                scores[hit.vector_id] = max(scores.get(hit.vector_id, 0.0), hit.score)

        vector_provider = self.vector_store.last_provider
        if scores:
            vector_provider = f"{vector_provider}/{embedding_provider}"
        return scores, vector_provider, self.vector_store.last_debug

    async def _local_vector_scores(self, queries: list[str], contents: list[str]) -> tuple[list[float], str]:
        if not contents:
            return [], self.embedding_provider.last_provider

        query_count = len(queries)
        if query_count <= 0:
            return [0.0 for _ in contents], self.embedding_provider.last_provider

        embeddings = await self.embedding_provider.embed_texts([*queries, *contents])
        provider = self.embedding_provider.last_provider
        if len(embeddings) != len(contents) + query_count:
            return [0.0 for _ in contents], provider

        query_vectors = embeddings[:query_count]
        content_vectors = embeddings[query_count:]
        scores = []
        for content_vector in content_vectors:
            scores.append(max(self._cosine_similarity(query_vector, content_vector) for query_vector in query_vectors))
        return scores, provider

    @staticmethod
    def _merge_rows(
        primary_rows: list[tuple[DocumentChunkTable, Any]],
        secondary_rows: list[tuple[DocumentChunkTable, Any]],
    ) -> list[tuple[DocumentChunkTable, Any]]:
        rows = []
        seen = set()
        for row in [*secondary_rows, *primary_rows]:
            chunk = row[0]
            if chunk.chunk_id in seen:
                continue
            seen.add(chunk.chunk_id)
            rows.append(row)
        return rows

    @staticmethod
    def _cosine_similarity(left: list[float], right: list[float]) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        dot = sum(left_value * right_value for left_value, right_value in zip(left, right))
        left_norm = math.sqrt(sum(value * value for value in left)) or 1.0
        right_norm = math.sqrt(sum(value * value for value in right)) or 1.0
        return dot / (left_norm * right_norm)

    @staticmethod
    def _normalize_scores(scores: list[float]) -> list[float]:
        positive_scores = [score for score in scores if score > 0]
        if not positive_scores:
            return [0.0 for _ in scores]
        max_score = max(positive_scores) or 1.0
        return [max(score, 0.0) / max_score for score in scores]

    @classmethod
    def _structured_score(
        cls,
        queries: list[str],
        structured_intent: dict[str, Any],
        content: str,
        metadata: dict,
    ) -> float:
        structured = metadata.get("structured") or metadata.get("structured_fields") or {}
        structured_text = cls._flatten_text(structured) or cls._flatten_text(cls._public_metadata(metadata))
        intent_text = cls._flatten_text(structured_intent)
        query_text = " ".join(value for value in [*queries, intent_text] if value)
        target_text = "\n".join(value for value in (content, structured_text) if value)
        if not query_text or not target_text:
            return 0.0

        query_terms = cls._terms(query_text)
        target_terms = cls._terms(target_text)
        field_terms = cls._terms(structured_text)
        if not query_terms or not target_terms:
            return 0.0

        overlap = len(query_terms.intersection(target_terms)) / math.sqrt(len(query_terms) * len(target_terms))
        field_overlap = 0.0
        if field_terms:
            field_overlap = len(query_terms.intersection(field_terms)) / math.sqrt(len(query_terms) * len(field_terms))

        query_quantities = cls._quantity_terms(query_text)
        target_quantities = cls._quantity_terms(target_text)
        quantity_score = 0.0
        if query_quantities and target_quantities:
            quantity_score = len(query_quantities.intersection(target_quantities)) / len(query_quantities)

        exact_bonus = 0.0
        for query in queries:
            if query and query in target_text:
                exact_bonus = 0.2
                break

        return min(0.55 * overlap + 0.25 * field_overlap + 0.25 * quantity_score + exact_bonus, 1.0)

    @classmethod
    def _flatten_text(cls, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, dict):
            return " ".join(
                part
                for key, item in value.items()
                for part in (str(key), cls._flatten_text(item))
                if part
            )
        if isinstance(value, list):
            return " ".join(cls._flatten_text(item) for item in value if item is not None)
        return str(value).strip()

    @staticmethod
    def _quantity_terms(text: str) -> set[str]:
        values = re.findall(r"\d+(?:\.\d+)?\s*(?:mm|cm|m|毫米|厘米|米|%)?", text or "", re.I)
        return {re.sub(r"\s+", "", value).lower() for value in values if value.strip()}

    @staticmethod
    def _rank_sources(score_breakdown: dict[str, float]) -> list[str]:
        sources = []
        if score_breakdown.get("vector", 0.0) > 0:
            sources.append("vector")
        if score_breakdown.get("keyword", 0.0) > 0:
            sources.append("keyword")
        if score_breakdown.get("structured", 0.0) > 0:
            sources.append("structured")
        return sources

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
