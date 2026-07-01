from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TypeVar

from loguru import logger

from agentchat.settings import app_settings
from agentchat.utils.project_paths import resolve_storage_path

T = TypeVar("T")


@dataclass
class VectorSearchResult:
    vector_id: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


class MilvusVectorStore:
    """RAG 知识库向量存储，支持 Milvus Lite 和 standalone Milvus。"""

    DEFAULT_COLLECTION_NAME = "global_knowledge_chunks"
    DEFAULT_LITE_PATH = ".rag_storage/milvus_lite.db"
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
        if not vector_rows or not embeddings:
            return None

        vector_dim = len(embeddings[0]) if embeddings and embeddings[0] else 0

        def _run() -> None:
            if vector_dim <= 0 or not self._ensure_collection(vector_dim):
                return
            document_ids = sorted({row["document_id"] for row in vector_rows})
            for document_id in document_ids:
                self.client.delete(
                    collection_name=self.collection_name,
                    filter=f'document_id == "{self._escape_filter_value(document_id)}"',
                )
            self._upsert_rows(vector_rows, embeddings)

        try:
            await self._run_lite_safe(_run)
        except Exception as err:
            self.last_provider = f"{self.mode}_upsert_failed"
            self.last_debug = {"provider": self.last_provider, "error": str(err)}
            logger.warning(f"Milvus 向量写入失败，检索将降级: {err}")
        return None

    async def upsert_chunks_incremental(self, vector_rows: list[dict[str, Any]], embeddings) -> None:
        if not vector_rows or not embeddings:
            return None

        vector_dim = len(embeddings[0]) if embeddings and embeddings[0] else 0

        def _run() -> None:
            if vector_dim <= 0 or not self._ensure_collection(vector_dim):
                return
            self._upsert_rows(vector_rows, embeddings)

        try:
            await self._run_lite_safe(_run)
        except Exception as err:
            self.last_provider = f"{self.mode}_upsert_failed"
            self.last_debug = {"provider": self.last_provider, "error": str(err)}
            logger.warning(f"Milvus 增量向量写入失败，检索将降级: {err}")
        return None

    async def delete_by_document(self, document_id: str) -> None:
        if not document_id:
            return

        def _run() -> None:
            if self.client is None and not self._connect():
                return
            self.client.delete(
                collection_name=self.collection_name,
                filter=f'document_id == "{self._escape_filter_value(document_id)}"',
            )

        try:
            await self._run_lite_safe(_run)
        except Exception as err:
            logger.warning(f"Milvus 删除文档向量失败 document_id={document_id}: {err}")

    def _upsert_rows(self, vector_rows: list[dict[str, Any]], embeddings) -> None:
        rows = []
        for row, embedding in zip(vector_rows, embeddings):
            if not embedding:
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
                }
            )

        if rows:
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

        results: list[VectorSearchResult] = []

        def _run() -> None:
            nonlocal results
            if not self._ensure_collection(len(query_vector)):
                return
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

        try:
            await self._run_lite_safe(_run)
            return results
        except Exception as err:
            self.last_provider = f"{self.mode}_search_failed"
            self.last_debug = {"provider": self.last_provider, "error": str(err)}
            logger.warning(f"Milvus 向量检索失败，检索将降级: {err}")
            return []

    async def _run_lite_safe(self, operation: Callable[[], T]) -> T:
        if self._uses_lite_short_session():
            return await self._run_in_lite_session(operation)
        return operation()

    async def _run_in_lite_session(self, operation: Callable[[], T]) -> T:
        import asyncio

        def _wrapped() -> T:
            with self._lite_session() as ready:
                if not ready:
                    raise RuntimeError(self.last_debug.get("error") or "Milvus Lite 连接失败")
                return operation()

        return await asyncio.to_thread(_wrapped)

    @contextmanager
    def _lite_session(self):
        from agentchat.infrastructure.vector_store.milvus_lite_lock import milvus_lite_lock

        db_path = Path(self._lite_uri(self._config()))
        lock_path = db_path.with_suffix(f"{db_path.suffix}.process.lock")
        timeout = float(self._config().get("lite_lock_timeout_seconds") or 60)

        try:
            with milvus_lite_lock(lock_path, timeout=timeout):
                if not self._connect():
                    yield False
                    return
                try:
                    yield True
                finally:
                    self._disconnect()
        except TimeoutError as err:
            self.client = None
            self.last_provider = "milvus_lite_lock_timeout"
            self.last_debug = {
                "provider": self.last_provider,
                "error": str(err),
                "hint": "Windows 上 Milvus Lite 不支持多进程同时持有 db 锁；"
                "当前已通过文件锁串行访问，若仍超时请检查是否有僵尸进程占用 .rag_storage。",
            }
            logger.warning(str(err))
            yield False

    def _uses_lite_short_session(self) -> bool:
        mode = str(self._config().get("mode") or "disabled").lower()
        if mode not in {"lite", "milvus_lite"}:
            return False
        short_session = self._config().get("lite_short_session")
        if short_session is not None:
            return bool(short_session)
        return True

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
            logger.warning(f"Milvus collection 准备失败，检索将降级: {err}")
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
            logger.warning(f"Milvus collection 加载失败，检索将降级: {err}")
            return False

    def _connect(self) -> bool:
        if self.client is not None:
            return True

        config = self._config()
        mode = str(config.get("mode") or "disabled").lower()
        if mode not in {"lite", "milvus_lite", "standalone", "milvus"}:
            self.mode = "disabled"
            self.last_provider = "disabled"
            return False

        try:
            from pymilvus import MilvusClient

            self.mode = "milvus_lite" if mode in {"lite", "milvus_lite"} else "milvus_standalone"
            self.collection_name = str(config.get("collection_name") or self.DEFAULT_COLLECTION_NAME)
            self.metric_type = str(config.get("metric_type") or "COSINE").upper()
            token = str(config.get("token") or "")

            if self.mode == "milvus_lite":
                self.uri = self._lite_uri(config)
                self.client = MilvusClient(uri=self.uri, token=token or None)
            else:
                host = str(config.get("host") or "127.0.0.1")
                port = str(config.get("port") or "19530")
                self.uri = str(config.get("uri") or f"http://{host}:{port}")
                self.client = MilvusClient(uri=self.uri, token=token or None)

            self.last_provider = self.mode
            self.last_debug = {
                "provider": self.mode,
                "collection": self.collection_name,
                "uri": self.uri,
                "short_session": self._uses_lite_short_session(),
            }
            logger.info("Milvus 已连接 mode={} uri={} collection={}", self.mode, self.uri, self.collection_name)
            return True
        except Exception as err:
            self.client = None
            self.last_provider = f"{mode}_connect_failed"
            self.last_debug = {
                "provider": self.last_provider,
                "error": str(err),
                "hint": "Milvus Lite 在 Windows 上若报锁冲突，请确认无多个进程长期占用同一 db 文件。",
            }
            logger.warning(f"Milvus 连接失败，检索将降级: {err}")
            return False

    def _disconnect(self) -> None:
        if self.client is None:
            return
        try:
            close = getattr(self.client, "close", None)
            if callable(close):
                close()
        except Exception as err:
            logger.debug(f"Milvus 断开连接时出现非致命错误: {err}")
        finally:
            self.client = None

    @staticmethod
    def _config() -> dict:
        rag = app_settings.rag
        if not rag:
            return {}
        config = getattr(rag, "vector_db", {}) or {}
        return config if isinstance(config, dict) else {}

    def _lite_uri(self, config: dict) -> str:
        raw_path = str(config.get("path") or config.get("uri") or self.DEFAULT_LITE_PATH)
        path = resolve_storage_path(raw_path, default_relative=self.DEFAULT_LITE_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        return str(path.resolve())

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
                    score=self._distance_to_score(distance),
                    metadata=entity,
                )
            )
        return results

    def _distance_to_score(self, distance: float) -> float:
        if self.metric_type == "COSINE":
            return max(0.0, min(1.0, 1.0 - distance))
        if self.metric_type == "L2":
            return 1.0 / (1.0 + max(distance, 0.0))
        return distance

    @staticmethod
    def _escape_filter_value(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')
