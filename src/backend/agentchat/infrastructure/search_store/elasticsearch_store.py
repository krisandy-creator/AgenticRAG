from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from agentchat.domains.indexing.exceptions import IndexingWriteError
from agentchat.settings import app_settings


@dataclass
class SparseSearchHit:
    chunk_id: str
    document_id: str
    score: float
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class ElasticsearchChunkStore:
    """Elasticsearch 全文索引存储，负责 chunk 稀疏召回。"""

    DEFAULT_INDEX_NAME = "rag_chunks"
    CONTENT_PREVIEW_LIMIT = 320

    INDEX_SETTINGS = {
        "settings": {
            "analysis": {
                "analyzer": {
                    "ik_analyzer": {
                        "type": "custom",
                        "tokenizer": "ik_smart",
                    }
                }
            }
        },
        "mappings": {
            "properties": {
                "chunk_id": {"type": "keyword"},
                "document_id": {"type": "keyword"},
                "file_name": {"type": "keyword"},
                "file_type": {"type": "keyword"},
                "page_no": {"type": "integer"},
                "chunk_type": {"type": "keyword"},
                "content": {"type": "text", "analyzer": "ik_analyzer"},
                "section_title": {"type": "text", "analyzer": "ik_analyzer"},
                "metadata": {"type": "object", "enabled": False},
                "permission_level": {"type": "integer"},
                "tenant_id": {"type": "keyword"},
                "department_id": {"type": "keyword"},
                "status": {"type": "keyword"},
                "index_version": {"type": "integer"},
                "created_at": {"type": "date"},
            }
        },
    }

    def __init__(self):
        self.index_name = self.DEFAULT_INDEX_NAME
        self.hosts = ""
        self.enabled = False
        self.last_provider = "disabled"
        self.last_debug: dict[str, Any] = {}
        self._client = None
        self._load_config()

    def _load_config(self) -> None:
        rag = app_settings.rag
        if not rag:
            return
        self.enabled = bool(getattr(rag, "enable_elasticsearch", False))
        config = getattr(rag, "elasticsearch", {}) or {}
        if not isinstance(config, dict):
            config = config.dict() if hasattr(config, "dict") else config.model_dump()
        self.hosts = str(config.get("hosts") or "http://127.0.0.1:9200")
        self.index_name = str(config.get("index_name") or self.DEFAULT_INDEX_NAME)

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not self.enabled:
            return None
        try:
            from elasticsearch import Elasticsearch

            self._client = Elasticsearch(self.hosts)
            self.last_provider = "elasticsearch"
            return self._client
        except Exception as err:
            self.last_provider = "elasticsearch_connect_failed"
            self.last_debug = {"provider": self.last_provider, "error": str(err)}
            logger.warning("Elasticsearch 连接失败: {}", err)
            return None

    def _get_client_or_raise(self, document_id: str, chunk_count: int):
        if not self.enabled:
            raise IndexingWriteError(
                "elasticsearch",
                document_id,
                chunk_count,
                "Elasticsearch 未启用，无法完成索引写入",
            )
        client = self._get_client()
        if client is None:
            raise IndexingWriteError(
                "elasticsearch",
                document_id,
                chunk_count,
                self.last_debug.get("error") or "Elasticsearch 连接失败",
            )
        return client

    async def ensure_index(self, *, document_id: str = "", chunk_count: int = 0, required: bool = False) -> bool:
        if not self.enabled:
            if required:
                raise IndexingWriteError(
                    "elasticsearch",
                    document_id,
                    chunk_count,
                    "Elasticsearch 未启用，无法完成索引写入",
                )
            return False

        def _run() -> bool:
            client = self._get_client()
            if client is None:
                return False
            if client.indices.exists(index=self.index_name):
                return True
            client.indices.create(index=self.index_name, body=self.INDEX_SETTINGS)
            logger.info("Elasticsearch 索引已创建 index={}", self.index_name)
            return True

        try:
            ok = await asyncio.to_thread(_run)
            if required and not ok:
                raise IndexingWriteError(
                    "elasticsearch",
                    document_id,
                    chunk_count,
                    self.last_debug.get("error") or "Elasticsearch 索引准备失败",
                )
            return ok
        except IndexingWriteError:
            raise
        except Exception as err:
            self.last_provider = "elasticsearch_ensure_index_failed"
            self.last_debug = {"provider": self.last_provider, "error": str(err)}
            if required:
                raise IndexingWriteError(
                    "elasticsearch",
                    document_id,
                    chunk_count,
                    f"Elasticsearch 创建索引失败: {err}",
                ) from err
            logger.warning("Elasticsearch 创建索引失败: {}", err)
            return False

    async def upsert_chunks(self, document, chunks: list, full_contents: list[str]) -> None:
        if not chunks:
            return
        if len(chunks) != len(full_contents):
            raise IndexingWriteError(
                "elasticsearch",
                document.document_id,
                len(chunks),
                "chunks 与 full_contents 数量不匹配",
            )

        document_id = document.document_id
        chunk_count = len(chunks)
        await self.ensure_index(document_id=document_id, chunk_count=chunk_count, required=True)

        now = datetime.now(timezone.utc).isoformat()
        actions = []
        for chunk, content in zip(chunks, full_contents):
            metadata = self._decode_metadata(chunk.metadata_json)
            section_title = self._section_title(metadata)
            actions.append(
                {
                    "chunk_id": chunk.chunk_id,
                    "document_id": chunk.document_id,
                    "file_name": document.file_name,
                    "file_type": document.file_type,
                    "page_no": chunk.page_no or 0,
                    "chunk_type": chunk.chunk_type,
                    "content": content,
                    "section_title": section_title,
                    "metadata": metadata,
                    "permission_level": int(chunk.permission_level),
                    "tenant_id": metadata.get("tenant_id") or "",
                    "department_id": metadata.get("department_id") or "",
                    "status": getattr(chunk, "status", None) or "active",
                    "index_version": int(getattr(chunk, "index_version", None) or 1),
                    "created_at": now,
                }
            )

        def _run() -> int:
            client = self._get_client_or_raise(document_id, chunk_count)
            from elasticsearch.helpers import bulk

            bulk_actions = [
                {
                    "_op_type": "index",
                    "_index": self.index_name,
                    "_id": row["chunk_id"],
                    "_source": row,
                }
                for row in actions
            ]
            success, errors = bulk(client, bulk_actions, raise_on_error=False)
            if success != len(actions):
                error_preview = errors[:3] if isinstance(errors, list) else errors
                raise IndexingWriteError(
                    "elasticsearch",
                    document_id,
                    chunk_count,
                    f"bulk 写入不完整: success={success} expected={len(actions)} errors={error_preview}",
                )
            self.last_provider = "elasticsearch"
            self.last_debug = {
                "provider": self.last_provider,
                "index": self.index_name,
                "upserted": success,
            }
            return success

        try:
            count = await asyncio.to_thread(_run)
            logger.info("Elasticsearch chunk 写入完成 index={} count={}", self.index_name, count)
        except IndexingWriteError:
            raise
        except Exception as err:
            self.last_provider = "elasticsearch_upsert_failed"
            self.last_debug = {"provider": self.last_provider, "error": str(err)}
            raise IndexingWriteError(
                "elasticsearch",
                document_id,
                chunk_count,
                f"Elasticsearch chunk 写入失败: {err}",
            ) from err

    async def delete_by_document(self, document_id: str) -> None:
        if not self.enabled or not document_id:
            return

        def _run() -> None:
            client = self._get_client()
            if client is None:
                return
            client.delete_by_query(
                index=self.index_name,
                body={"query": {"term": {"document_id": document_id}}},
                refresh=True,
            )

        try:
            await asyncio.to_thread(_run)
            logger.info("Elasticsearch 已删除文档 chunk document_id={}", document_id)
        except Exception as err:
            logger.warning("Elasticsearch 删除文档 chunk 失败 document_id={}: {}", document_id, err)

    async def search(self, query: str, access_level: int, top_k: int = 100) -> list[SparseSearchHit]:
        if not self.enabled or not query.strip():
            return []

        body = {
            "size": top_k,
            "timeout": "5s",
            "query": {
                "bool": {
                    "must": [
                        {
                            "match": {
                                "content": {
                                    "query": query,
                                    "analyzer": "ik_smart",
                                    "operator": "or",
                                }
                            }
                        }
                    ],
                    "filter": [
                        {"term": {"status": "active"}},
                        {"range": {"permission_level": {"lte": int(access_level)}}},
                    ],
                }
            },
        }

        def _run() -> list[SparseSearchHit]:
            client = self._get_client()
            if client is None:
                return []
            response = client.search(index=self.index_name, body=body)
            hits = []
            for item in response.get("hits", {}).get("hits", []):
                source = item.get("_source") or {}
                chunk_id = str(source.get("chunk_id") or item.get("_id") or "")
                if not chunk_id:
                    continue
                hits.append(
                    SparseSearchHit(
                        chunk_id=chunk_id,
                        document_id=str(source.get("document_id") or ""),
                        score=float(item.get("_score") or 0.0),
                        content=str(source.get("content") or ""),
                        metadata=source.get("metadata") or {},
                    )
                )
            self.last_provider = "elasticsearch"
            self.last_debug = {
                "provider": self.last_provider,
                "index": self.index_name,
                "query": query,
                "returned": len(hits),
            }
            return hits

        try:
            return await asyncio.to_thread(_run)
        except Exception as err:
            self.last_provider = "elasticsearch_search_failed"
            self.last_debug = {"provider": self.last_provider, "error": str(err)}
            logger.warning("Elasticsearch 检索失败: {}", err)
            return []

    async def get_contents(self, chunk_ids: list[str]) -> dict[str, str]:
        if not self.enabled or not chunk_ids:
            return {}

        def _run() -> dict[str, str]:
            client = self._get_client()
            if client is None:
                return {}
            response = client.mget(index=self.index_name, body={"ids": chunk_ids})
            contents = {}
            for doc in response.get("docs", []):
                if not doc.get("found"):
                    continue
                chunk_id = str(doc.get("_id") or "")
                source = doc.get("_source") or {}
                contents[chunk_id] = str(source.get("content") or "")
            return contents

        try:
            return await asyncio.to_thread(_run)
        except Exception as err:
            logger.warning("Elasticsearch 批量读取 chunk 失败: {}", err)
            return {}

    @staticmethod
    def _decode_metadata(metadata_json: str | None) -> dict:
        import json

        try:
            data = json.loads(metadata_json or "{}")
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    @classmethod
    def _section_title(cls, metadata: dict) -> str:
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
    def content_preview(cls, content: str) -> str:
        return (content or "")[: cls.CONTENT_PREVIEW_LIMIT]
