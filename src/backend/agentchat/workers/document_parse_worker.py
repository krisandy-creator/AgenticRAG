import json
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

from agentchat.domains.documents.entities import DocumentPageTable
from agentchat.domains.documents.parse_trace import ParseTraceCollector
from agentchat.domains.documents.repositories import DocumentRepository
from agentchat.domains.indexing.chunking import ChunkingService, TextChunk
from agentchat.domains.indexing.services import IndexingService
from agentchat.infrastructure.storage.oss_storage import OssStorageAdapter
from agentchat.services.rag.doc_parser.pdf_config import load_pdf_parse_config
from agentchat.services.rag.doc_parser.pdf_parse_pipeline import PDFParsePipeline


async def parse_document_task(document_id: str) -> None:
    document = DocumentRepository.get_document(document_id)
    job = DocumentRepository.get_latest_job(document_id)
    if not document or not job:
        return

    storage = OssStorageAdapter()
    DocumentRepository.update_document_status(document_id, "parsing")
    DocumentRepository.update_job_status(job.job_id, "running")

    try:
        data = await storage.download(document.oss_key)
        if document.file_type == "pdf":
            await PDFParsePipeline(load_pdf_parse_config()).run(document, job, data)
        else:
            await _parse_non_pdf(document, job, data)
        DocumentRepository.update_job_status(job.job_id, "success")
        DocumentRepository.update_document_status(document_id, "ready")
    except Exception as err:
        DocumentRepository.update_job_status(job.job_id, "failed", str(err))
        DocumentRepository.update_document_status(document_id, "failed", str(err))
        job = DocumentRepository.get_latest_job(document_id)
        if job:
            ParseTraceCollector(job.job_id).record("job", "failed", str(err))


async def _parse_non_pdf(document, job, data: bytes) -> None:
    chunker = ChunkingService()
    indexing = IndexingService()
    trace = ParseTraceCollector(job.job_id)

    pages, chunks = await _parse_by_type(document, data, chunker)
    DocumentRepository.clear_parse_artifacts(document.document_id)
    DocumentRepository.append_pages(document.document_id, pages)
    trace.record("parse", "success", f"非 PDF 解析完成，chunk={len(chunks)}")
    DocumentRepository.update_job_progress(
        job.job_id,
        parsed_pages=len(pages),
        indexed_chunks=len(chunks),
        current_stage="indexing",
    )
    await indexing.index_chunks(document, chunks)
    DocumentRepository.update_job_progress(job.job_id, indexed_chunks=len(chunks), current_stage="done")


async def _parse_by_type(document, data: bytes, chunker: ChunkingService):
    if document.file_type == "docx":
        text = _extract_docx_text(data)
        chunks = chunker.split_text(text, page_no=None, chunk_type="text", metadata={"parser": "docx_plain_text"})
        return [], chunks

    if document.file_type == "excel":
        markdown = _extract_xlsx_markdown(document.file_name, data)
        chunks = chunker.split_text(markdown, page_no=None, chunk_type="excel_markdown", metadata={"parser": "excel_markdown"})
        return [], chunks

    raise ValueError(f"不支持的文档类型: {document.file_type}")


def _extract_docx_text(data: bytes) -> str:
    with zipfile.ZipFile(BytesIO(data)) as archive:
        xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = []
    for paragraph in root.findall(".//w:p", namespace):
        texts = [node.text or "" for node in paragraph.findall(".//w:t", namespace)]
        line = "".join(texts).strip()
        if line:
            paragraphs.append(line)
    return "\n".join(paragraphs)


def _extract_xlsx_markdown(file_name: str, data: bytes) -> str:
    if Path(file_name).suffix.lower() == ".xls":
        return "# Excel 文件\n\n第一版原型暂不解析二进制 .xls，请转存为 .xlsx 后上传。"

    with zipfile.ZipFile(BytesIO(data)) as archive:
        shared_strings = _read_shared_strings(archive)
        sheet_names = _read_sheet_names(archive)
        sheet_files = [name for name in archive.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")]
        sections = [f"# {file_name} 语义索引"]
        for index, sheet_file in enumerate(sheet_files[:10]):
            sheet_name = sheet_names[index] if index < len(sheet_names) else f"Sheet{index + 1}"
            rows = _read_sheet_rows(archive.read(sheet_file), shared_strings)
            sections.append(_sheet_to_markdown(sheet_name, rows))
        return "\n\n".join(sections)


def _read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    namespace = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    values = []
    for item in root.findall(".//m:si", namespace):
        values.append("".join(node.text or "" for node in item.findall(".//m:t", namespace)))
    return values


def _read_sheet_names(archive: zipfile.ZipFile) -> list[str]:
    root = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    namespace = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    return [sheet.attrib.get("name", "") for sheet in root.findall(".//m:sheet", namespace)]


def _read_sheet_rows(xml: bytes, shared_strings: list[str]) -> list[list[str]]:
    root = ElementTree.fromstring(xml)
    namespace = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows = []
    for row in root.findall(".//m:row", namespace)[:30]:
        values = []
        for cell in row.findall("m:c", namespace):
            value_node = cell.find("m:v", namespace)
            value = value_node.text if value_node is not None else ""
            if cell.attrib.get("t") == "s" and value.isdigit():
                value = shared_strings[int(value)] if int(value) < len(shared_strings) else value
            values.append(value)
        rows.append(values)
    return rows


def _sheet_to_markdown(sheet_name: str, rows: list[list[str]]) -> str:
    row_count = len(rows)
    col_count = max((len(row) for row in rows), default=0)
    headers = rows[0] if rows else []
    samples = rows[1:6]
    sample_text = "\n".join("- " + " | ".join(row) for row in samples) or "- 无样例"
    return (
        f"## Sheet: {sheet_name}\n"
        f"- 行数样例: {row_count}\n"
        f"- 列数: {col_count}\n"
        f"- 表头: {', '.join(headers) if headers else '未识别'}\n"
        f"- 样例值:\n{sample_text}"
    )


def _build_vision_pages(document_id: str, vision_pages: list[dict], chunker: ChunkingService):
    pages = []
    chunks: list[TextChunk] = []

    for page in vision_pages:
        page_no = int(page.get("page_no") or len(pages) + 1)
        text = str(page.get("text") or "")
        blocks = page.get("blocks") or []
        pages.append(
            DocumentPageTable(
                document_id=document_id,
                page_no=page_no,
                text=text,
                blocks_json=json.dumps(blocks, ensure_ascii=False),
            )
        )

        for chunk in page.get("chunks") or []:
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

        if text and not any(chunk.page_no == page_no for chunk in chunks):
            chunks.extend(
                chunker.split_text(
                    text,
                    page_no=page_no,
                    chunk_type="vision_page",
                    metadata={"parser": "qwen_vl"},
                )
            )

    return pages, chunks
