from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from agentchat.domains.indexing.exceptions import IndexingWriteError
from agentchat.settings import app_settings


@dataclass
class VectorSearchResult:
    vector_id: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


class MilvusVectorStore:
    """远程 Milvus 向量存储。"""

    DEFAULT_COLLECTION_NAME = "global_knowledge_chunks"
    VECTOR_FIELD = "embedding"
    PRIMARY_FIELD = "vector_id"

    def __init__(self):
        self.client = None
        self.collection_name = self.DEFAULT_COLLECTION_NAME
        self.metric_type = "COSINE"
        self.mode = "disabled"
        self.uri = ""
        self.last_provider = "disabled"
        self.last_debug: dict[str, Any] = {}

    async def upsert_chunks(self, vector_rows: list[dict[str, Any]], embeddings) -> None:
        self._validate_write_inputs(vector_rows, embeddings)
        document_id = vector_rows[0]["document_id"]
        chunk_count = len(vector_rows)
        vector_dim = len(embeddings[0]) if embeddings[0] else 0
        if vector_dim <= 0:
            raise IndexingWriteError("milvus", document_id, chunk_count, "向量维度非法")

        self._ensure_collection_or_raise(vector_dim, document_id=document_id, chunk_count=chunk_count)

        document_ids = sorted({row["document_id"] for row in vector_rows})
        try:
            for doc_id in document_ids:
                self.client.delete(
                    collection_name=self.collection_name,
                    filter=f'document_id == "{self._escape_filter_value(doc_id)}"',
                )
            self._upsert_rows(vector_rows, embeddings)
        except IndexingWriteError:
            raise
        except Exception as err:
            raise IndexingWriteError("milvus", document_id, chunk_count, f"向量写入失败: {err}") from err

    async def upsert_chunks_incremental(self, vector_rows: list[dict[str, Any]], embeddings) -> None:
        self._validate_write_inputs(vector_rows, embeddings)
        document_id = vector_rows[0]["document_id"]
        chunk_count = len(vector_rows)
        vector_dim = len(embeddings[0]) if embeddings[0] else 0
        if vector_dim <= 0:
            raise IndexingWriteError("milvus", document_id, chunk_count, "向量维度非法")

        self._ensure_collection_or_raise(vector_dim, document_id=document_id, chunk_count=chunk_count)
        try:
            self._upsert_rows(vector_rows, embeddings)
        except IndexingWriteError:
            raise
        except Exception as err:
            raise IndexingWriteError("milvus", document_id, chunk_count, f"向量增量写入失败: {err}") from err

    @staticmethod
    def _validate_write_inputs(vector_rows: list[dict[str, Any]], embeddings) -> None:
        if not vector_rows or not embeddings:
            raise IndexingWriteError("milvus", "", 0, "vector_rows 或 embeddings 为空")
        if len(vector_rows) != len(embeddings):
            document_id = vector_rows[0].get("document_id", "")
            raise IndexingWriteError(
                "milvus",
                str(document_id),
                len(vector_rows),
                f"vector_rows({len(vector_rows)}) 与 embeddings({len(embeddings)}) 数量不匹配",
            )

    async def delete_by_document(self, document_id: str) -> None:
        if not document_id:
            return
        if self.client is None and not self._connect():
            return
        try:
            self.client.delete(
                collection_name=self.collection_name,
                filter=f'document_id == "{self._escape_filter_value(document_id)}"',
            )
        except Exception as err:
            logger.warning("Milvus 删除文档向量失败 document_id={}: {}", document_id, err)

    def _upsert_rows(self, vector_rows: list[dict[str, Any]], embeddings) -> None:
        rows = []
        skipped = 0
        for row, embedding in zip(vector_rows, embeddings):
            if not embedding:
                skipped += 1
                continue
            rows.append(
                {
                    self.PRIMARY_FIELD: row["vector_id"],
                    self.VECTOR_FIELD: embedding,
                    "chunk_id": row["chunk_id"],
                    "document_id": row["document_id"],
                    "permission_level": int(row["permission_level"]),
                    "page_no": int(row.get("page_no") or 0),
                    "chunk_type": row["chunk_type"],
                    "index_version": int(row.get("index_version") or 1),
                }
            )

        if skipped:
            document_id = vector_rows[0]["document_id"]
            raise IndexingWriteError(
                "milvus",
                document_id,
                len(vector_rows),
                f"存在 {skipped} 个空 embedding，拒绝写入",
            )
        if not rows:
            document_id = vector_rows[0]["document_id"]
            raise IndexingWriteError("milvus", document_id, len(vector_rows), "没有可写入的向量行")

        self.client.upsert(collection_name=self.collection_name, data=rows)
        self.last_provider = self.mode
        self.last_debug = {
            "provider": self.mode,
            "collection": self.collection_name,
            "uri": self.uri,
            "upserted": len(rows),
            "documents": sorted({row["document_id"] for row in vector_rows}),
        }
        logger.info("Milvus 向量写入完成 collection={} count={}", self.collection_name, len(rows))

    async def search(self, query_vector: list[float], filters: dict, top_k: int) -> list[VectorSearchResult]:
        if not query_vector:
            return []
        if not self._ensure_collection(len(query_vector)):
            return []

        try:
            raw_hits = self.client.search(
                collection_name=self.collection_name,
                data=[query_vector],
                limit=top_k,
                filter=self._filter_expression(filters),
                output_fields=[
                    self.PRIMARY_FIELD,
                    "chunk_id",
                    "document_id",
                    "permission_level",
                    "page_no",
                    "chunk_type",
                    "index_version",
                ],
            )
            results = self._parse_hits(raw_hits[0] if raw_hits else [])
            self.last_provider = self.mode
            self.last_debug = {
                "provider": self.mode,
                "collection": self.collection_name,
                "uri": self.uri,
                "returned": len(results),
            }
            return results
        except Exception as err:
            self.last_provider = f"{self.mode}_search_failed"
            self.last_debug = {"provider": self.last_provider, "error": str(err)}
            logger.warning("Milvus 向量检索失败: {}", err)
            return []

    def _ensure_collection_or_raise(self, vector_dim: int, *, document_id: str, chunk_count: int) -> None:
        config = self._config()
        mode = str(config.get("mode") or "disabled").lower()
        if mode not in {"standalone", "milvus"}:
            raise IndexingWriteError(
                "milvus",
                document_id,
                chunk_count,
                "Milvus 未配置为 standalone 模式，无法完成索引写入",
            )
        if not self._ensure_collection(vector_dim):
            raise IndexingWriteError(
                "milvus",
                document_id,
                chunk_count,
                self.last_debug.get("error") or "Milvus collection 准备失败",
            )

    def _ensure_collection(self, vector_dim: int) -> bool:
        if self.client is None and not self._connect():
            return False

        try:
            if self.client.has_collection(self.collection_name):
                return self._load_collection()

            from pymilvus import DataType, MilvusClient

            schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=True)
            schema.add_field(field_name=self.PRIMARY_FIELD, datatype=DataType.VARCHAR, is_primary=True, max_length=256)
            schema.add_field(field_name=self.VECTOR_FIELD, datatype=DataType.FLOAT_VECTOR, dim=vector_dim)
            schema.add_field(field_name="chunk_id", datatype=DataType.VARCHAR, max_length=128)
            schema.add_field(field_name="document_id", datatype=DataType.VARCHAR, max_length=128)
            schema.add_field(field_name="permission_level", datatype=DataType.INT64)
            schema.add_field(field_name="page_no", datatype=DataType.INT64)
            schema.add_field(field_name="chunk_type", datatype=DataType.VARCHAR, max_length=64)
            schema.add_field(field_name="index_version", datatype=DataType.INT64)

            index_params = self.client.prepare_index_params()
            index_params.add_index(
                field_name=self.VECTOR_FIELD,
                metric_type=self.metric_type,
                index_type="AUTOINDEX",
                index_name="embedding_index",
            )
            self.client.create_collection(
                collection_name=self.collection_name,
                schema=schema,
                index_params=index_params,
            )
            logger.info(
                "Milvus collection 已创建 collection={} dim={} mode={}",
                self.collection_name,
                vector_dim,
                self.mode,
            )
            return self._load_collection()
        except Exception as err:
            self.last_provider = f"{self.mode}_collection_failed"
            self.last_debug = {"provider": self.last_provider, "error": str(err)}
            logger.warning("Milvus collection 准备失败: {}", err)
            return False

    def _load_collection(self) -> bool:
        try:
            state = None
            get_load_state = getattr(self.client, "get_load_state", None)
            if callable(get_load_state):
                load_state = get_load_state(collection_name=self.collection_name)
                state = load_state.get("state") if isinstance(load_state, dict) else load_state
                if state is not None and "Loaded" in str(state):
                    return True

            self.client.load_collection(collection_name=self.collection_name)
            logger.info(
                "Milvus collection 已加载 collection={} mode={} previous_state={}",
                self.collection_name,
                self.mode,
                state,
            )
            return True
        except Exception as err:
            self.last_provider = f"{self.mode}_collection_load_failed"
            self.last_debug = {"provider": self.last_provider, "error": str(err)}
            logger.warning("Milvus collection 加载失败: {}", err)
            return False

    def _connect(self) -> bool:
        if self.client is not None:
            return True

        config = self._config()
        mode = str(config.get("mode") or "disabled").lower()
        if mode not in {"standalone", "milvus"}:
            self.mode = "disabled"
            self.last_provider = "disabled"
            return False

        try:
            from pymilvus import MilvusClient

            self.mode = "milvus_standalone"
            self.collection_name = str(config.get("collection_name") or self.DEFAULT_COLLECTION_NAME)
            self.metric_type = str(config.get("metric_type") or "COSINE").upper()
            token = str(config.get("token") or "")
            self.uri = str(config.get("uri") or f"http://{config.get('host') or '127.0.0.1'}:{config.get('port') or '19530'}")
            self.client = MilvusClient(uri=self.uri, token=token or None)
            self.last_provider = self.mode
            self.last_debug = {
                "provider": self.mode,
                "collection": self.collection_name,
                "uri": self.uri,
            }
            logger.info("Milvus 已连接 mode={} uri={} collection={}", self.mode, self.uri, self.collection_name)
            return True
        except Exception as err:
            self.client = None
            self.last_provider = f"{mode}_connect_failed"
            self.last_debug = {"provider": self.last_provider, "error": str(err)}
            logger.warning("Milvus 连接失败: {}", err)
            return False

    @staticmethod
    def _config() -> dict:
        rag = app_settings.rag
        if not rag:
            return {}
        config = getattr(rag, "vector_db", {}) or {}
        return config if isinstance(config, dict) else {}

    @staticmethod
    def _filter_expression(filters: dict | None) -> str | None:
        if not filters:
            return None

        expressions = []
        access_level = filters.get("access_level")
        if access_level is not None:
            expressions.append(f"permission_level <= {int(access_level)}")

        document_id = filters.get("document_id")
        if document_id:
            expressions.append(f'document_id == "{MilvusVectorStore._escape_filter_value(str(document_id))}"')

        return " and ".join(expressions) if expressions else None

    def _parse_hits(self, raw_hits: list[dict]) -> list[VectorSearchResult]:
        results = []
        for hit in raw_hits:
            entity = hit.get("entity") or {}
            vector_id = str(hit.get(self.PRIMARY_FIELD) or hit.get("id") or entity.get(self.PRIMARY_FIELD) or "")
            if not vector_id:
                continue
            distance = float(hit.get("distance", hit.get("score", 0.0)) or 0.0)
            results.append(
                VectorSearchResult(
                    vector_id=vector_id,
                    score=self._raw_metric_to_score(distance),
                    metadata=entity,
                )
            )
        return results

    def _raw_metric_to_score(self, value: float) -> float:
        if self.metric_type in {"COSINE", "IP"}:
            return max(0.0, value)
        if self.metric_type == "L2":
            return 1.0 / (1.0 + max(value, 0.0))
        return value

    @staticmethod
    def _escape_filter_value(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')
