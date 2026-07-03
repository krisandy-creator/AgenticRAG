from __future__ import annotations

import hashlib
import json
from typing import Any

from loguru import logger

from agentchat.domains.indexing.chunking import TextChunk
from agentchat.domains.indexing.entities import DocumentChunkTable
from agentchat.domains.indexing.exceptions import IndexingWriteError
from agentchat.domains.indexing.repositories import ChunkRepository
from agentchat.infrastructure.models.embedding_provider import EmbeddingProvider
from agentchat.infrastructure.search_store.elasticsearch_store import ElasticsearchChunkStore
from agentchat.infrastructure.vector_store.milvus_store import MilvusVectorStore


class ChunkIndexWriter:
    """负责 chunk 写入：Embedding → MySQL manifest → Milvus → Elasticsearch。"""

    DEFAULT_INDEX_VERSION = 1

    def __init__(self):
        self.embedding_provider = EmbeddingProvider()
        self.vector_store = MilvusVectorStore()
        self.sparse_store = ElasticsearchChunkStore()

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
                await self.vector_store.delete_by_document(document.document_id)
                await self.sparse_store.delete_by_document(document.document_id)
            return []

        db_chunks: list[DocumentChunkTable] = []
        full_contents: list[str] = []
        all_embeddings: list[list[float]] = []

        for batch_start in range(0, len(chunks), embedding_batch_size):
            batch = chunks[batch_start : batch_start + embedding_batch_size]
            embeddings = await self.embedding_provider.embed_texts_batched(
                [chunk.content for chunk in batch],
                api_batch_size=embedding_api_batch_size,
                max_concurrency=embedding_max_concurrency,
            )
            if len(embeddings) != len(batch):
                raise IndexingWriteError(
                    "embedding",
                    document.document_id,
                    len(batch),
                    f"embedding 数量({len(embeddings)}) 与 batch({len(batch)}) 不匹配",
                )
            all_embeddings.extend(embeddings)
            for offset, chunk in enumerate(batch):
                global_index = start_index + batch_start + offset
                embedding = embeddings[offset]
                if not embedding:
                    raise IndexingWriteError(
                        "embedding",
                        document.document_id,
                        len(batch),
                        f"第 {global_index} 个 chunk embedding 为空",
                    )
                metadata = {
                    **chunk.metadata,
                    "file_name": document.file_name,
                    "file_type": document.file_type,
                    "embedding_dim": len(embedding),
                }
                full_content = chunk.content
                db_chunks.append(
                    DocumentChunkTable(
                        document_id=document.document_id,
                        page_no=chunk.page_no,
                        chunk_type=chunk.chunk_type,
                        content=ElasticsearchChunkStore.content_preview(full_content),
                        content_hash=hashlib.sha256(full_content.encode("utf-8")).hexdigest(),
                        vector_id=f"{document.document_id}_{global_index}",
                        permission_level=document.permission_level,
                        index_version=self.DEFAULT_INDEX_VERSION,
                        status="active",
                        metadata_json=json.dumps(metadata, ensure_ascii=False),
                    )
                )
                full_contents.append(full_content)

        if len(db_chunks) != len(all_embeddings):
            raise IndexingWriteError(
                "indexing",
                document.document_id,
                len(db_chunks),
                "db_chunks 与 embeddings 数量不匹配",
            )

        vector_rows = self._vector_rows_from_chunks(db_chunks)
        try:
            if replace and start_index == 0:
                await self.vector_store.delete_by_document(document.document_id)
                await self.sparse_store.delete_by_document(document.document_id)
                await self.vector_store.upsert_chunks(vector_rows, all_embeddings)
                await self.sparse_store.upsert_chunks(document, db_chunks, full_contents)
                ChunkRepository.replace_chunks(document.document_id, db_chunks)
            else:
                await self.vector_store.upsert_chunks_incremental(vector_rows, all_embeddings)
                await self.sparse_store.upsert_chunks(document, db_chunks, full_contents)
                ChunkRepository.append_chunks(document.document_id, db_chunks)
        except IndexingWriteError:
            raise
        except Exception as err:
            raise IndexingWriteError(
                "indexing",
                document.document_id,
                len(db_chunks),
                str(err),
            ) from err

        logger.info(
            "Chunk 索引写入完成 document_id={} count={} replace={}",
            document.document_id,
            len(db_chunks),
            replace and start_index == 0,
        )
        return db_chunks

    @staticmethod
    def _vector_rows_from_chunks(chunks: list[DocumentChunkTable]) -> list[dict[str, Any]]:
        return [
            {
                "vector_id": chunk.vector_id,
                "chunk_id": chunk.chunk_id,
                "document_id": chunk.document_id,
                "permission_level": chunk.permission_level,
                "page_no": chunk.page_no or 0,
                "chunk_type": chunk.chunk_type,
                "index_version": chunk.index_version,
            }
            for chunk in chunks
        ]
