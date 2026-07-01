"""企业级 PDF 解析流水线：页窗口、checkpoint、空白页/文本层分流、微批索引。"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from agentchat.domains.documents.entities import DocumentPageTable
from agentchat.domains.documents.parse_trace import ParseTraceCollector
from agentchat.domains.documents.repositories import DocumentRepository
from agentchat.domains.indexing.chunking import ChunkingService, TextChunk
from agentchat.domains.indexing.services import IndexingService
from agentchat.infrastructure.concurrency.redis_limiter import LocalConcurrencyLimiter, RedisConcurrencyLimiter
from agentchat.infrastructure.vector_store.milvus_store import MilvusVectorStore
from agentchat.services.rag.doc_parser.multimodal_pdf import MultimodalPDFParser, RenderedPDFPage
from agentchat.services.rag.doc_parser.pdf_config import PDFParseConfig, load_pdf_parse_config
from agentchat.services.rag.doc_parser.pdf_page_analyzer import analyze_page, get_page_count


class PDFParsePipeline:
    VISION_SLOT_KEY = "rag:vision:inflight"

    def __init__(
        self,
        config: PDFParseConfig | None = None,
        parser: MultimodalPDFParser | None = None,
    ):
        self.config = config or load_pdf_parse_config()
        self.parser = parser or MultimodalPDFParser()
        self.indexing = IndexingService()
        self.vector_store = MilvusVectorStore()
        self.limiter = RedisConcurrencyLimiter()

    async def run(self, document, job, data: bytes) -> None:
        if not self.config.enabled:
            raise ValueError("PDF 多模态解析未启用")

        trace = ParseTraceCollector(job.job_id)
        total_pages = get_page_count(data)
        if total_pages > self.config.max_pages:
            raise ValueError(f"PDF 共 {total_pages} 页，超过上限 {self.config.max_pages} 页")

        checkpoint = DocumentRepository.load_job_checkpoint(job)
        next_page = int(checkpoint.get("next_page") or 1)
        chunk_seq = int(checkpoint.get("chunk_seq") or 0)

        trace.record("init", "success", f"PDF 共 {total_pages} 页，从第 {next_page} 页开始处理")

        if next_page == 1:
            DocumentRepository.clear_parse_artifacts(document.document_id)
            await self.vector_store.delete_by_document(document.document_id)
            DocumentRepository.update_job_progress(
                job.job_id,
                total_pages=total_pages,
                parsed_pages=0,
                indexed_chunks=0,
                current_stage="parsing",
                checkpoint={"next_page": 1, "chunk_seq": 0},
            )

        import fitz

        document_obj = fitz.open(stream=data, filetype="pdf")
        chunker = ChunkingService()
        indexed_total = chunk_seq

        try:
            while next_page <= total_pages:
                window_end = min(next_page + self.config.page_batch_size - 1, total_pages)
                DocumentRepository.update_job_progress(
                    job.job_id,
                    current_stage="parsing",
                    checkpoint={"next_page": next_page, "chunk_seq": chunk_seq},
                )
                trace.record(
                    "window",
                    "running",
                    f"处理页窗口 {next_page}-{window_end}",
                    extra={"window_start": next_page, "window_end": window_end},
                )

                pages, chunks = await self._process_window(
                    document_obj,
                    document.document_id,
                    next_page,
                    window_end,
                    chunker,
                    trace,
                )

                if pages:
                    DocumentRepository.append_pages(document.document_id, pages)

                if chunks:
                    DocumentRepository.update_job_progress(job.job_id, current_stage="indexing")
                    await self.indexing.index_chunks_batch(
                        document,
                        chunks,
                        start_index=chunk_seq,
                        replace=False,
                        embedding_batch_size=self.config.embedding_batch_size,
                        embedding_max_concurrency=self.config.embedding_max_concurrency,
                        embedding_api_batch_size=self.config.embedding_api_batch_size,
                    )
                    chunk_seq += len(chunks)
                    indexed_total = chunk_seq

                DocumentRepository.update_job_progress(
                    job.job_id,
                    parsed_pages=window_end,
                    indexed_chunks=indexed_total,
                    current_stage="parsing",
                    checkpoint={"next_page": window_end + 1, "chunk_seq": chunk_seq},
                )
                trace.record(
                    "window",
                    "success",
                    f"页窗口 {next_page}-{window_end} 完成，累计 chunk={chunk_seq}",
                    extra={"parsed_pages": window_end, "indexed_chunks": chunk_seq},
                )
                next_page = window_end + 1
        finally:
            document_obj.close()

        DocumentRepository.update_job_progress(
            job.job_id,
            parsed_pages=total_pages,
            indexed_chunks=indexed_total,
            current_stage="done",
            checkpoint={"next_page": total_pages + 1, "chunk_seq": chunk_seq},
        )
        trace.record("complete", "success", f"PDF 解析完成，共 {total_pages} 页，{indexed_total} 个 chunk")

    async def _process_window(
        self,
        document_obj,
        document_id: str,
        start_page: int,
        end_page: int,
        chunker: ChunkingService,
        trace: ParseTraceCollector,
    ) -> tuple[list[DocumentPageTable], list[TextChunk]]:
        pages: list[DocumentPageTable] = []
        chunks: list[TextChunk] = []
        vision_pages: list[tuple[int, RenderedPDFPage | None]] = []

        with tempfile.TemporaryDirectory(prefix="pdf_window_") as temp_dir:
            output_dir = Path(temp_dir)
            for page_no in range(start_page, end_page + 1):
                page = document_obj[page_no - 1]
                analysis = analyze_page(
                    page,
                    page_no,
                    text_layer_min_chars=self.config.text_layer_min_chars,
                    blank_max_nonwhite_ratio=self.config.blank_page_max_nonwhite_ratio,
                )

                if analysis.is_blank:
                    trace.record(
                        "blank_page",
                        "skipped",
                        f"第 {page_no} 页为空白页，跳过 Vision 与索引",
                        page_no=page_no,
                        extra={"nonwhite_ratio": round(analysis.nonwhite_ratio, 6)},
                    )
                    pages.append(self._blank_page_record(document_id, page_no, analysis.nonwhite_ratio))
                    continue

                if analysis.use_text_layer:
                    trace.record(
                        "text_layer",
                        "success",
                        f"第 {page_no} 页使用文本层解析（{len(analysis.text_layer)} 字符）",
                        page_no=page_no,
                    )
                    page_record, page_chunks = self._build_text_layer_page(
                        document_id,
                        page_no,
                        analysis.text_layer,
                        chunker,
                    )
                    pages.append(page_record)
                    chunks.extend(page_chunks)
                    continue

                rendered = self.parser.render_page(page, page_no, output_dir, dpi=self.config.dpi)
                vision_pages.append((page_no, rendered))

            if vision_pages:
                parsed = await self._parse_vision_pages(vision_pages, trace)
                for page_no, page_data in parsed:
                    if not page_data:
                        raise ValueError(f"第 {page_no} 页 Vision 解析失败（已重试 {self.config.vision_max_retries} 次）")
                    pages.append(self._vision_page_record(document_id, page_data))
                    chunks.extend(self._vision_page_chunks(page_data, chunker))

        pages.sort(key=lambda item: item.page_no)
        return pages, chunks

    async def _parse_vision_pages(
        self,
        vision_pages: list[tuple[int, RenderedPDFPage | None]],
        trace: ParseTraceCollector,
    ) -> list[tuple[int, dict | None]]:
        local_sem = LocalConcurrencyLimiter.semaphore(
            "vision_per_doc",
            self.config.vision_max_concurrency_per_doc,
        )

        async def parse_one(page_no: int, rendered: RenderedPDFPage | None) -> tuple[int, dict | None]:
            if rendered is None:
                return page_no, None
            async with local_sem:
                acquired = False
                try:
                    await self.limiter.acquire(self.VISION_SLOT_KEY, self.config.vision_global_limit)
                    acquired = True
                except Exception:
                    pass
                try:
                    result = await self.parser.parse_page_with_retry(
                        rendered,
                        max_retries=self.config.vision_max_retries,
                        trace=trace,
                    )
                    return page_no, result
                finally:
                    if acquired:
                        try:
                            await self.limiter.release(self.VISION_SLOT_KEY)
                        except Exception:
                            pass

        tasks = [parse_one(page_no, rendered) for page_no, rendered in vision_pages]
        results = await asyncio.gather(*tasks)
        return list(results)

    @staticmethod
    def _blank_page_record(document_id: str, page_no: int, nonwhite_ratio: float) -> DocumentPageTable:
        blocks = [
            {
                "type": "blank_page",
                "parser": "pdf_analyzer",
                "page_no": page_no,
                "nonwhite_ratio": nonwhite_ratio,
            }
        ]
        return DocumentPageTable(
            document_id=document_id,
            page_no=page_no,
            text="",
            blocks_json=json.dumps(blocks, ensure_ascii=False),
        )

    @staticmethod
    def _build_text_layer_page(
        document_id: str,
        page_no: int,
        text: str,
        chunker: ChunkingService,
    ) -> tuple[DocumentPageTable, list[TextChunk]]:
        title = f"第 {page_no} 页"
        markdown = f"# {title}\n\n{text.strip()}"
        blocks = [
            {
                "type": "text_layer_page",
                "parser": "pdf_text_layer",
                "page_no": page_no,
            }
        ]
        page = DocumentPageTable(
            document_id=document_id,
            page_no=page_no,
            text=markdown,
            blocks_json=json.dumps(blocks, ensure_ascii=False),
        )
        chunks = chunker.split_text(
            markdown,
            page_no=page_no,
            chunk_type="pdf_text_layer",
            metadata={"parser": "pdf_text_layer", "structured": {"kind": "text_layer", "page_no": page_no}},
        )
        return page, chunks

    @staticmethod
    def _vision_page_record(document_id: str, page_data: dict) -> DocumentPageTable:
        page_no = int(page_data.get("page_no") or 0)
        text = str(page_data.get("text") or "")
        blocks = page_data.get("blocks") or []
        return DocumentPageTable(
            document_id=document_id,
            page_no=page_no,
            text=text,
            blocks_json=json.dumps(blocks, ensure_ascii=False),
        )

    @staticmethod
    def _vision_page_chunks(page_data: dict, chunker: ChunkingService) -> list[TextChunk]:
        page_no = int(page_data.get("page_no") or 0)
        text = str(page_data.get("text") or "")
        chunks: list[TextChunk] = []

        for chunk in page_data.get("chunks") or []:
            content = str(chunk.get("content") or "").strip()
            if not content:
                continue
            chunks.append(
                TextChunk(
                    content=content,
                    page_no=int(chunk.get("page_no") or page_no),
                    chunk_type=str(chunk.get("chunk_type") or "vision_fact"),
                    metadata={**(chunk.get("metadata") or {}), "parser": "qwen_vl"},
                )
            )

        if text and not chunks:
            chunks.extend(
                chunker.split_text(
                    text,
                    page_no=page_no,
                    chunk_type="vision_page",
                    metadata={"parser": "qwen_vl"},
                )
            )
        return chunks


pdf_parse_pipeline = PDFParsePipeline()
