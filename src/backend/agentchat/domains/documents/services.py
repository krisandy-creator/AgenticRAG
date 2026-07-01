import hashlib
import json
from collections import Counter
from pathlib import Path
from uuid import uuid4

from fastapi import BackgroundTasks, HTTPException, UploadFile

from agentchat.domains.documents.entities import DocumentParseJobTable, DocumentPageTable, DocumentTable
from agentchat.domains.documents.repositories import DocumentRepository
from agentchat.domains.indexing.entities import DocumentChunkTable
from agentchat.infrastructure.storage.oss_storage import OssStorageAdapter
from agentchat.workers.document_parse_worker import parse_document_task


SUPPORTED_FILE_TYPES = {
    ".docx": ("docx", "docx"),
    ".pdf": ("pdf", "pdf_ocr"),
    ".xlsx": ("excel", "excel_markdown"),
    ".xls": ("excel", "excel_markdown"),
}


class DocumentService:
    def __init__(self):
        self.storage = OssStorageAdapter()

    @staticmethod
    def _detect_file(filename: str) -> tuple[str, str]:
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_FILE_TYPES:
            raise HTTPException(status_code=400, detail="仅支持 docx、PDF、Excel 文件")
        return SUPPORTED_FILE_TYPES[suffix]

    async def upload_document(
        self,
        file: UploadFile,
        permission_level: int,
        uploaded_by: str,
        background_tasks: BackgroundTasks,
    ) -> DocumentTable:
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="上传文件为空")

        file_type, parser_type = self._detect_file(file.filename or "")
        content_hash = hashlib.sha256(data).hexdigest()
        object_key = f"enterprise-rag/{uuid4().hex}_{file.filename}"
        await self.storage.upload(object_key, data)

        document = DocumentTable(
            file_name=file.filename or object_key,
            file_type=file_type,
            content_hash=content_hash,
            file_size=len(data),
            oss_key=object_key,
            permission_level=permission_level,
            uploaded_by=uploaded_by,
        )
        job = DocumentParseJobTable(document_id=document.document_id, parser_type=parser_type)
        created = DocumentRepository.create_document(document, job)
        background_tasks.add_task(parse_document_task, created.document_id)
        return created

    def list_documents(self, access_level: int, is_admin: bool) -> list[dict]:
        return [document.to_dict() for document in DocumentRepository.list_documents(access_level, is_admin)]

    def get_document_for_user(self, document_id: str, access_level: int, is_admin: bool) -> DocumentTable:
        document = DocumentRepository.get_document(document_id)
        if not document or document.status == "deleted":
            raise HTTPException(status_code=404, detail="文档不存在")
        if not is_admin and document.permission_level > access_level:
            raise HTTPException(status_code=403, detail="无权访问该文档")
        return document

    async def get_download_url(self, document_id: str, access_level: int, is_admin: bool) -> str:
        document = self.get_document_for_user(document_id, access_level, is_admin)
        return await self.storage.presigned_url(document.oss_key)

    def get_parse_job(self, document_id: str, access_level: int, is_admin: bool) -> dict | None:
        document = self.get_document_for_user(document_id, access_level, is_admin)
        job = DocumentRepository.get_latest_job(document_id)
        if not job:
            return None
        result = job.to_dict()
        result["trace"] = self._build_parse_trace(document, job)
        return result

    def delete_document(self, document_id: str, is_admin: bool) -> None:
        if not is_admin:
            raise HTTPException(status_code=403, detail="只有管理员可以删除文档")
        DocumentRepository.delete_document(document_id)

    @staticmethod
    def _build_parse_trace(document: DocumentTable, job: DocumentParseJobTable) -> dict:
        pages = DocumentRepository.list_pages(document.document_id)
        chunks = DocumentRepository.list_chunks(document.document_id)
        parser_counts: Counter[str] = Counter()
        chunk_type_counts: Counter[str] = Counter()

        chunk_summaries = []
        for chunk in chunks:
            metadata = DocumentService._decode_json_object(chunk.metadata_json)
            parser = str(metadata.get("parser") or "unknown")
            parser_counts[parser] += 1
            chunk_type_counts[chunk.chunk_type] += 1
            if len(chunk_summaries) < 8:
                chunk_summaries.append(DocumentService._chunk_summary(chunk, metadata))

        page_summaries = [DocumentService._page_summary(page) for page in pages]
        ocr_pages = [page for page in page_summaries if page["parser"] == "paddleocr"]
        vision_pages = [page for page in page_summaries if page["parser"] == "qwen_vl"]
        text_char_count = sum(len(page.text or "") for page in pages) or sum(len(chunk.content or "") for chunk in chunks)

        summary = {
            "page_count": len(pages),
            "chunk_count": len(chunks),
            "ocr_page_count": len(ocr_pages),
            "vision_page_count": len(vision_pages),
            "text_char_count": text_char_count,
            "parser_counts": dict(parser_counts),
            "chunk_type_counts": dict(chunk_type_counts),
        }

        return {
            "summary": summary,
            "events": DocumentService._parse_events(document, job, summary),
            "pages": page_summaries,
            "chunks": chunk_summaries,
        }

    @staticmethod
    def _parse_events(document: DocumentTable, job: DocumentParseJobTable, summary: dict) -> list[dict]:
        failed = job.status == "failed"
        success = job.status == "success"
        running = job.status == "running"
        has_pages = summary["page_count"] > 0
        has_chunks = summary["chunk_count"] > 0

        return [
            {
                "stage": "接收文件",
                "status": "success",
                "message": f"已接收 {document.file_name}，大小 {document.file_size} 字节。",
                "at": DocumentService._to_iso(document.created_at),
            },
            {
                "stage": "解析文本",
                "status": DocumentService._stage_status(success or has_pages or has_chunks, running, failed),
                "message": DocumentService._parse_message(job, summary),
                "at": DocumentService._to_iso(job.started_at),
            },
            {
                "stage": "切分 Chunk",
                "status": DocumentService._stage_status(has_chunks, running, failed),
                "message": f"已生成 {summary['chunk_count']} 个 chunk。",
                "at": DocumentService._to_iso(job.finished_at if success else None),
            },
            {
                "stage": "写入索引",
                "status": DocumentService._stage_status(success, running and has_chunks, failed),
                "message": "索引写入完成，可用于问答检索。" if success else "等待解析完成后写入索引。",
                "at": DocumentService._to_iso(job.finished_at),
            },
        ]

    @staticmethod
    def _parse_message(job: DocumentParseJobTable, summary: dict) -> str:
        if job.status == "failed":
            return job.error_message or "解析失败，暂无错误详情。"
        if summary.get("vision_page_count"):
            return f"多模态解析 {summary['vision_page_count']} 页，累计 {summary['text_char_count']} 个字符。"
        if summary["ocr_page_count"]:
            return f"OCR 识别 {summary['ocr_page_count']} 页，累计 {summary['text_char_count']} 个字符。"
        if summary["page_count"]:
            return f"文本解析 {summary['page_count']} 页，累计 {summary['text_char_count']} 个字符。"
        if summary["chunk_count"]:
            return f"解析为结构化文本，累计 {summary['text_char_count']} 个字符。"
        return "解析任务已创建，等待后台 worker 处理。"

    @staticmethod
    def _stage_status(done: bool, running: bool, failed: bool) -> str:
        if failed:
            return "failed"
        if done:
            return "success"
        if running:
            return "running"
        return "pending"

    @staticmethod
    def _page_summary(page: DocumentPageTable) -> dict:
        blocks = DocumentService._decode_json_list(page.blocks_json)
        parser = "pymupdf_text"
        if blocks:
            first_block = blocks[0] if isinstance(blocks[0], dict) else {}
            parser = str(first_block.get("parser") or first_block.get("type") or "paddleocr")
        return {
            "page_no": page.page_no,
            "text_length": len(page.text or ""),
            "block_count": len(blocks),
            "parser": parser,
            "sample_blocks": [DocumentService._block_summary(block) for block in blocks[:6]],
        }

    @staticmethod
    def _chunk_summary(chunk: DocumentChunkTable, metadata: dict) -> dict:
        return {
            "chunk_id": chunk.chunk_id,
            "page_no": chunk.page_no,
            "chunk_type": chunk.chunk_type,
            "parser": metadata.get("parser") or "unknown",
            "length": len(chunk.content or ""),
            "preview": (chunk.content or "")[:120],
        }

    @staticmethod
    def _block_summary(block: object) -> dict:
        if not isinstance(block, dict):
            return {"text": str(block)[:120], "confidence": None, "bbox": None}
        confidence = block.get("confidence", block.get("score"))
        return {
            "text": str(block.get("text", ""))[:120],
            "confidence": confidence,
            "bbox": block.get("bbox") or block.get("box"),
        }

    @staticmethod
    def _decode_json_object(value: str | None) -> dict:
        try:
            data = json.loads(value or "{}")
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _decode_json_list(value: str | None) -> list:
        try:
            data = json.loads(value or "[]")
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []

    @staticmethod
    def _to_iso(value) -> str | None:
        return value.isoformat() if value else None
