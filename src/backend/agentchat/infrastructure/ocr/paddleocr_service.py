import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from agentchat.infrastructure.storage.oss_storage import OssStorageAdapter


class ParsePdfRequest(BaseModel):
    file_url: str = Field(description="PDF 源文件 URL、local:// 路径或 OSS object key")
    dpi: int = Field(default=180, ge=96, le=300, description="PDF 渲染 DPI")
    max_pages: int | None = Field(default=None, ge=1, description="调试时可限制解析页数")


class PaddleOcrService:
    """本地 PaddleOCR 服务，负责把扫描 PDF 转成页码、文本块和坐标。"""

    def __init__(self):
        self._ocr = None

    async def parse_pdf(self, request: ParsePdfRequest) -> dict[str, Any]:
        pdf_bytes = await self._load_pdf_bytes(request.file_url)
        pages = self._render_and_ocr(pdf_bytes, dpi=request.dpi, max_pages=request.max_pages)
        return {"pages": pages, "page_count": len(pages)}

    async def _load_pdf_bytes(self, file_url: str) -> bytes:
        if file_url.startswith("http://") or file_url.startswith("https://"):
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.get(file_url)
                response.raise_for_status()
                return response.content

        if file_url.startswith("local://"):
            object_key = _local_object_key(file_url)
            for candidate in _local_candidates(object_key):
                if candidate.exists():
                    return candidate.read_bytes()
            raise HTTPException(status_code=404, detail=f"本地 OCR 文件不存在: {object_key}")

        return await OssStorageAdapter().download(file_url)

    def _render_and_ocr(self, pdf_bytes: bytes, dpi: int, max_pages: int | None) -> list[dict[str, Any]]:
        fitz = _load_fitz()
        ocr = self._get_ocr()
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
        page_total = min(len(document), max_pages or len(document))
        pages: list[dict[str, Any]] = []

        with tempfile.TemporaryDirectory(prefix="paddleocr_pdf_") as temp_dir:
            temp_path = Path(temp_dir)
            matrix = fitz.Matrix(dpi / 72, dpi / 72)
            for page_index in range(page_total):
                page = document.load_page(page_index)
                pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                image_path = temp_path / f"page_{page_index + 1}.png"
                pixmap.save(str(image_path))

                raw_result = _run_ocr(ocr, str(image_path))
                blocks = _normalize_ocr_result(raw_result)
                pages.append(
                    {
                        "page_no": page_index + 1,
                        "text": "\n".join(block["text"] for block in blocks),
                        "blocks": blocks,
                    }
                )

        return pages

    def _get_ocr(self):
        if self._ocr is not None:
            return self._ocr

        try:
            from paddleocr import PaddleOCR
        except Exception as err:
            raise HTTPException(
                status_code=503,
                detail=f"PaddleOCR 未安装或不可用: {err}",
            ) from err

        lang = os.getenv("PADDLEOCR_LANG", "ch")
        try:
            self._ocr = PaddleOCR(
                lang=lang,
                device=os.getenv("PADDLEOCR_DEVICE", "cpu"),
                enable_mkldnn=False,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
        except (TypeError, ValueError):
            self._ocr = PaddleOCR(use_angle_cls=True, lang=lang)
        return self._ocr


def _load_fitz():
    try:
        import fitz

        return fitz
    except Exception as err:
        raise HTTPException(
            status_code=503,
            detail=f"PyMuPDF 未安装或不可用，无法渲染 PDF: {err}",
        ) from err


def _run_ocr(ocr: Any, image_path: str) -> Any:
    try:
        return ocr.ocr(image_path, cls=True)
    except TypeError:
        return ocr.ocr(image_path)


def _normalize_ocr_result(raw_result: Any) -> list[dict[str, Any]]:
    if isinstance(raw_result, list) and raw_result and isinstance(raw_result[0], dict):
        blocks = []
        for page_result in raw_result:
            texts = page_result.get("rec_texts") or []
            scores = page_result.get("rec_scores") or []
            polys = page_result.get("rec_polys") or page_result.get("dt_polys") or []
            for index, text_value in enumerate(texts):
                text = str(text_value).strip()
                if not text:
                    continue
                score = scores[index] if index < len(scores) else 0.0
                bbox = polys[index] if index < len(polys) else []
                blocks.append(
                    {
                        "text": text,
                        "score": float(score),
                        "bbox": _to_jsonable_bbox(bbox),
                    }
                )
        return blocks

    lines = raw_result
    if isinstance(raw_result, list) and len(raw_result) == 1 and isinstance(raw_result[0], list):
        lines = raw_result[0]
    if not isinstance(lines, list):
        return []

    blocks = []
    for line in lines:
        if not isinstance(line, (list, tuple)) or len(line) < 2:
            continue
        bbox = line[0]
        payload = line[1]
        if not isinstance(payload, (list, tuple)) or len(payload) < 2:
            continue
        text = str(payload[0]).strip()
        if not text:
            continue
        blocks.append(
            {
                "text": text,
                "score": float(payload[1]),
                "bbox": bbox,
            }
        )
    return blocks


def _to_jsonable_bbox(value: Any) -> Any:
    if hasattr(value, "tolist"):
        return value.tolist()
    return value


def _local_object_key(file_url: str) -> str:
    parsed = urlparse(file_url)
    value = f"{parsed.netloc}{parsed.path}"
    return unquote(value).lstrip("/")


def _local_candidates(object_key: str) -> list[Path]:
    backend_root = Path(__file__).resolve().parents[3]
    repo_root = backend_root.parents[1]
    return [
        Path(".rag_storage") / object_key,
        backend_root / ".rag_storage" / object_key,
        repo_root / "src" / "backend" / ".rag_storage" / object_key,
    ]


ocr_service = PaddleOcrService()
app = FastAPI(title="Enterprise RAG PaddleOCR Service", version="v0.1")


@app.get("/health")
async def health_check():
    return {"status": "OK"}


@app.post("/parse_pdf")
async def parse_pdf(request: ParsePdfRequest):
    return await ocr_service.parse_pdf(request)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("PADDLEOCR_HOST", "127.0.0.1"), port=int(os.getenv("PADDLEOCR_PORT", "8791")))
