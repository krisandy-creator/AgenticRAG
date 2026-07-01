import base64
import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from agentchat.core.models.manager import ModelManager
from agentchat.settings import app_settings


@dataclass
class RenderedPDFPage:
    page_no: int
    image_path: Path
    width: int
    height: int


class MultimodalPDFParser:
    """将扫描/图形型 PDF 页面解析为可检索的结构化文本。"""

    DEFAULT_DPI = 180
    DEFAULT_MAX_PAGES = 500
    RANGE_PATTERN = re.compile(r"(?P<min>\d+(?:\.\d+)?)\s*[~～\-]\s*(?P<max>\d+(?:\.\d+)?)\s*(?P<unit>mm|cm|m)?", re.I)

    def __init__(self, client=None):
        self.client = client

    async def parse_pdf_bytes(self, data: bytes) -> list[dict]:
        if not self._is_enabled():
            return []

        with tempfile.TemporaryDirectory(prefix="multimodal_pdf_") as temp_dir:
            pages = self._render_pdf_bytes(data, Path(temp_dir))
            return await self._parse_rendered_pages(pages)

    async def parse_pdf_file(self, file_path: str) -> list[dict]:
        if not self._is_enabled():
            return []

        with tempfile.TemporaryDirectory(prefix="multimodal_pdf_") as temp_dir:
            pages = self._render_pdf_file(file_path, Path(temp_dir))
            return await self._parse_rendered_pages(pages)

    async def parse_pdf_file_to_markdown(self, file_path: str) -> str:
        pages = await self.parse_pdf_file(file_path)
        return "\n\n".join(page.get("text", "") for page in pages if page.get("text")).strip()

    def _is_enabled(self) -> bool:
        config = self._config()
        return bool(config.get("enabled", True))

    def _config(self) -> dict:
        rag = app_settings.rag
        if not rag:
            return {}
        config = getattr(rag, "multimodal_parse", {}) or {}
        return config if isinstance(config, dict) else {}

    def _dpi(self) -> int:
        return int(self._config().get("dpi", self.DEFAULT_DPI))

    def _max_pages(self) -> int:
        return int(self._config().get("max_pages", self.DEFAULT_MAX_PAGES))

    def _render_pdf_bytes(self, data: bytes, output_dir: Path) -> list[RenderedPDFPage]:
        try:
            import fitz
        except ImportError:
            logger.warning("PyMuPDF 未安装，无法渲染 PDF 页面进行多模态解析")
            return []

        try:
            with fitz.open(stream=data, filetype="pdf") as document:
                return self._render_document(document, output_dir)
        except Exception as err:
            logger.warning(f"PDF 页面渲染失败，跳过多模态解析: {err}")
            return []

    def _render_pdf_file(self, file_path: str, output_dir: Path) -> list[RenderedPDFPage]:
        try:
            import fitz
        except ImportError:
            logger.warning("PyMuPDF 未安装，无法渲染 PDF 页面进行多模态解析")
            return []

        try:
            with fitz.open(file_path) as document:
                return self._render_document(document, output_dir)
        except Exception as err:
            logger.warning(f"PDF 页面渲染失败，跳过多模态解析: {err}")
            return []

    def _render_document(self, document, output_dir: Path, start_page: int = 1, end_page: int | None = None) -> list[RenderedPDFPage]:
        output_dir.mkdir(parents=True, exist_ok=True)
        matrix_scale = self._dpi() / 72
        matrix = self._fitz_matrix(matrix_scale)
        pages: list[RenderedPDFPage] = []
        max_page = end_page or min(document.page_count, self._max_pages())

        for index in range(start_page, max_page + 1):
            if index > document.page_count:
                break
            page = document[index - 1]
            rendered = self.render_page(page, index, output_dir, dpi=self._dpi(), matrix=matrix)
            if rendered:
                pages.append(rendered)
        return pages

    def render_page(
        self,
        page,
        page_no: int,
        output_dir: Path,
        *,
        dpi: int | None = None,
        matrix=None,
    ) -> RenderedPDFPage | None:
        output_dir.mkdir(parents=True, exist_ok=True)
        scale = (dpi or self._dpi()) / 72
        render_matrix = matrix or self._fitz_matrix(scale)
        pixmap = page.get_pixmap(matrix=render_matrix, alpha=False)
        image_path = output_dir / f"page_{page_no}.png"
        pixmap.save(image_path)
        return RenderedPDFPage(page_no=page_no, image_path=image_path, width=pixmap.width, height=pixmap.height)

    @staticmethod
    def _fitz_matrix(scale: float):
        import fitz

        return fitz.Matrix(scale, scale)

    async def _parse_rendered_pages(self, pages: list[RenderedPDFPage]) -> list[dict]:
        parsed_pages = []
        for page in pages:
            parsed = await self.parse_page_with_retry(page)
            if parsed:
                parsed_pages.append(parsed)
        return parsed_pages

    async def parse_page_with_retry(
        self,
        page: RenderedPDFPage,
        *,
        max_retries: int = 3,
        trace=None,
    ) -> dict | None:
        last_error = ""
        for attempt in range(1, max_retries + 1):
            try:
                parsed = await self._parse_page(page)
                if parsed:
                    if trace is not None:
                        trace.record(
                            "vision",
                            "success",
                            f"第 {page.page_no} 页 Vision 解析成功",
                            page_no=page.page_no,
                            attempt=attempt,
                        )
                    return parsed
                last_error = "解析结果为空"
            except Exception as err:
                last_error = str(err)
                logger.warning(f"第 {page.page_no} 页多模态解析失败（第 {attempt}/{max_retries} 次）: {err}")

            if trace is not None:
                trace.record(
                    "vision",
                    "retry" if attempt < max_retries else "failed",
                    f"第 {page.page_no} 页 Vision 解析失败：{last_error}",
                    page_no=page.page_no,
                    attempt=attempt,
                )

        return None

    async def _parse_page(self, page: RenderedPDFPage) -> dict | None:
        try:
            raw_response = await self._request_vision_model(page)
        except Exception as err:
            logger.warning(f"第 {page.page_no} 页多模态解析失败: {err}")
            return None

        payload = self._extract_json(raw_response)
        text = self._payload_to_markdown(page.page_no, payload, raw_response)
        chunks = self._payload_to_chunks(page.page_no, payload, text)
        blocks = [
            {
                "type": "vision_page",
                "parser": "qwen_vl",
                "page_no": page.page_no,
                "image": {"width": page.width, "height": page.height},
                "payload": payload,
                "raw_text": raw_response[:4000],
            }
        ]

        return {
            "page_no": page.page_no,
            "text": text,
            "blocks": blocks,
            "chunks": chunks,
        }

    async def _request_vision_model(self, page: RenderedPDFPage) -> str:
        client = self.client or ModelManager.get_qwen_vl_model()
        image_type = page.image_path.suffix.lstrip(".") or "png"
        image_base64 = base64.b64encode(page.image_path.read_bytes()).decode("utf-8")
        response = await client.ainvoke(
            input=[
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "text",
                            "text": "你是企业知识库文档解析器，擅长从图片型 PDF 中提取表格、流程图、图示标注和数值规则。",
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/{image_type};base64,{image_base64}"}},
                        {"type": "text", "text": self._prompt(page.page_no)},
                    ],
                },
            ],
        )
        return self._content_to_text(response.content)

    @staticmethod
    def _prompt(page_no: int) -> str:
        return (
            f"请解析第 {page_no} 页。只输出严格 JSON，不要 Markdown，不要代码块。\n"
            "需要识别页面标题、正文、表格、流程步骤、图示说明、箭头/标注关系和关键数值范围。\n"
            "如果看到 300~1200mm、50~75mm 这类范围，必须显式拆出 min、max、unit。\n"
            "如果看到表格，请保留每一行的参数、说明、备注、范围/值等字段。\n"
            "如果看到流程图，请按步骤输出 order、name、description、related_labels。\n"
            "读不清的内容写 uncertain，不要编造。\n"
            "JSON 结构如下：\n"
            "{\n"
            '  "title": "",\n'
            '  "summary": "",\n'
            '  "tables": [{"title": "", "headers": [], "rows": []}],\n'
            '  "facts": [{"subject": "", "attribute": "", "value": "", "unit": "", "min": "", "max": "", "evidence": ""}],\n'
            '  "steps": [{"order": 1, "name": "", "description": "", "related_labels": []}],\n'
            '  "figures": [{"title": "", "description": "", "labels": []}],\n'
            '  "warnings": []\n'
            "}"
        )

    @staticmethod
    def _content_to_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            values = []
            for item in content:
                if isinstance(item, dict):
                    values.append(str(item.get("text") or item.get("content") or item))
                else:
                    values.append(str(item))
            return "\n".join(values)
        return str(content)

    def _extract_json(self, raw_text: str) -> dict:
        text = raw_text.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        start = text.find("{")
        if start < 0:
            return {"summary": raw_text}

        try:
            data, _ = json.JSONDecoder().raw_decode(text[start:])
            return data if isinstance(data, dict) else {"items": data}
        except json.JSONDecodeError:
            logger.warning("多模态解析结果不是合法 JSON，将按纯文本入库")
            return {"summary": raw_text}

    def _payload_to_markdown(self, page_no: int, payload: dict, raw_text: str) -> str:
        title = self._as_text(payload.get("title")) or f"第 {page_no} 页"
        lines = [f"# 第 {page_no} 页：{title}"]
        summary = self._as_text(payload.get("summary"))
        if summary:
            lines.extend(["", f"页面摘要：{summary}"])

        for table in self._as_list(payload.get("tables")):
            table_title = self._as_text(table.get("title")) if isinstance(table, dict) else ""
            lines.extend(["", f"## 表格：{table_title or '未命名表格'}"])
            for row in self._table_rows(table):
                row_text = self._row_to_text(row)
                if row_text:
                    lines.append(f"- {row_text}")
                    range_text = self._range_text(row_text)
                    if range_text:
                        lines.append(f"  - {range_text}")

        facts = self._as_list(payload.get("facts"))
        if facts:
            lines.extend(["", "## 关键事实"])
            for fact in facts:
                fact_text = self._fact_to_text(fact)
                if fact_text:
                    lines.append(f"- {fact_text}")

        steps = self._as_list(payload.get("steps"))
        if steps:
            lines.extend(["", "## 流程步骤"])
            for step in steps:
                step_text = self._step_to_text(step)
                if step_text:
                    lines.append(f"- {step_text}")

        figures = self._as_list(payload.get("figures"))
        if figures:
            lines.extend(["", "## 图示说明"])
            for figure in figures:
                figure_text = self._figure_to_text(figure)
                if figure_text:
                    lines.append(f"- {figure_text}")

        if len(lines) <= 1:
            lines.append(raw_text)
        return "\n".join(lines).strip()

    def _payload_to_chunks(self, page_no: int, payload: dict, page_text: str) -> list[dict]:
        title = self._as_text(payload.get("title")) or f"第 {page_no} 页"
        chunks: list[dict] = []

        summary = self._as_text(payload.get("summary"))
        if summary:
            chunks.append(
                self._chunk(
                    page_no,
                    "vision_page_summary",
                    f"页面 {page_no} > {title}\n页面摘要：{summary}",
                    {
                        "title": title,
                        "structured": {
                            "kind": "page_summary",
                            "page_no": page_no,
                            "title": title,
                            "summary": summary,
                        },
                    },
                )
            )

        for table in self._as_list(payload.get("tables")):
            table_title = self._as_text(table.get("title")) if isinstance(table, dict) else ""
            for row_index, row in enumerate(self._table_rows(table), start=1):
                row_text = self._row_to_text(row)
                if not row_text:
                    continue
                range_text = self._range_text(row_text)
                content = f"页面 {page_no} > {title} > 表格：{table_title or '未命名表格'}\n行 {row_index}：{row_text}"
                if range_text:
                    content += f"\n{range_text}"
                chunks.append(
                    self._chunk(
                        page_no,
                        "vision_table_row",
                        content,
                        {
                            "title": title,
                            "table_title": table_title,
                            "row_index": row_index,
                            "structured": self._table_row_structured(page_no, title, table_title, row_index, row),
                        },
                    )
                )

        for fact_index, fact in enumerate(self._as_list(payload.get("facts")), start=1):
            fact_text = self._fact_to_text(fact)
            if fact_text:
                chunks.append(
                    self._chunk(
                        page_no,
                        "vision_fact",
                        f"页面 {page_no} > {title}\n事实 {fact_index}：{fact_text}",
                        {
                            "title": title,
                            "fact_index": fact_index,
                            "structured": self._fact_structured(page_no, title, fact_index, fact),
                        },
                    )
                )

        for step_index, step in enumerate(self._as_list(payload.get("steps")), start=1):
            step_text = self._step_to_text(step)
            if step_text:
                chunks.append(
                    self._chunk(
                        page_no,
                        "vision_step",
                        f"页面 {page_no} > {title}\n流程：{step_text}",
                        {
                            "title": title,
                            "step_index": step_index,
                            "structured": self._step_structured(page_no, title, step_index, step),
                        },
                    )
                )

        for figure_index, figure in enumerate(self._as_list(payload.get("figures")), start=1):
            figure_text = self._figure_to_text(figure)
            if figure_text:
                chunks.append(
                    self._chunk(
                        page_no,
                        "vision_figure",
                        f"页面 {page_no} > {title}\n图示：{figure_text}",
                        {
                            "title": title,
                            "figure_index": figure_index,
                            "structured": self._figure_structured(page_no, title, figure_index, figure),
                        },
                    )
                )

        if not chunks and page_text:
            chunks.append(
                self._chunk(
                    page_no,
                    "vision_page",
                    page_text,
                    {
                        "title": title,
                        "structured": {"kind": "page_text", "page_no": page_no, "title": title, "text": page_text},
                    },
                )
            )
        return chunks

    @staticmethod
    def _chunk(page_no: int, chunk_type: str, content: str, metadata: dict | None = None) -> dict:
        return {
            "content": content.strip(),
            "page_no": page_no,
            "chunk_type": chunk_type,
            "metadata": {"parser": "qwen_vl", **(metadata or {})},
        }

    def _table_rows(self, table: object) -> list:
        if not isinstance(table, dict):
            return []
        rows = table.get("rows") or []
        headers = table.get("headers") or []
        normalized = []
        for row in self._as_list(rows):
            if isinstance(row, dict):
                normalized.append(row)
            elif isinstance(row, list) and headers:
                normalized.append({str(headers[index]): value for index, value in enumerate(row) if index < len(headers)})
            else:
                normalized.append(row)
        return normalized

    def _row_to_text(self, row: object) -> str:
        if isinstance(row, dict):
            parts = []
            for key, value in row.items():
                value_text = self._as_text(value)
                if value_text:
                    parts.append(f"{key}：{value_text}")
            return "；".join(parts)
        if isinstance(row, list):
            return "；".join(self._as_text(item) for item in row if self._as_text(item))
        return self._as_text(row)

    def _table_row_structured(self, page_no: int, title: str, table_title: str, row_index: int, row: object) -> dict:
        row_payload = row if isinstance(row, dict) else {"value": self._as_text(row)}
        row_text = self._row_to_text(row)
        return {
            "kind": "table_row",
            "page_no": page_no,
            "title": title,
            "table_title": table_title,
            "row_index": row_index,
            "row": row_payload,
            "range": self._range_payload(row_text),
        }

    def _fact_to_text(self, fact: object) -> str:
        if not isinstance(fact, dict):
            return self._as_text(fact)

        labels = {
            "subject": "主体",
            "attribute": "属性",
            "value": "值",
            "unit": "单位",
            "min": "最小值",
            "max": "最大值",
            "evidence": "依据",
        }
        parts = []
        for key, label in labels.items():
            value = self._as_text(fact.get(key))
            if value:
                parts.append(f"{label}：{value}")
        return "；".join(parts)

    def _fact_structured(self, page_no: int, title: str, fact_index: int, fact: object) -> dict:
        if not isinstance(fact, dict):
            return {
                "kind": "fact",
                "page_no": page_no,
                "title": title,
                "fact_index": fact_index,
                "raw": self._as_text(fact),
            }
        return {
            "kind": "fact",
            "page_no": page_no,
            "title": title,
            "fact_index": fact_index,
            "subject": self._as_text(fact.get("subject")),
            "attribute": self._as_text(fact.get("attribute")),
            "value": self._as_text(fact.get("value")),
            "unit": self._as_text(fact.get("unit")),
            "min": self._as_text(fact.get("min")),
            "max": self._as_text(fact.get("max")),
            "evidence": self._as_text(fact.get("evidence")),
            "raw": fact,
        }

    def _step_to_text(self, step: object) -> str:
        if not isinstance(step, dict):
            return self._as_text(step)
        order = self._as_text(step.get("order"))
        name = self._as_text(step.get("name"))
        description = self._as_text(step.get("description"))
        labels = "、".join(self._as_text(item) for item in self._as_list(step.get("related_labels")) if self._as_text(item))
        parts = []
        if order:
            parts.append(f"步骤{order}")
        if name:
            parts.append(name)
        if description:
            parts.append(description)
        if labels:
            parts.append(f"相关标注：{labels}")
        return "；".join(parts)

    def _step_structured(self, page_no: int, title: str, step_index: int, step: object) -> dict:
        if not isinstance(step, dict):
            return {
                "kind": "step",
                "page_no": page_no,
                "title": title,
                "step_index": step_index,
                "raw": self._as_text(step),
            }
        return {
            "kind": "step",
            "page_no": page_no,
            "title": title,
            "step_index": step_index,
            "order": self._as_text(step.get("order")),
            "name": self._as_text(step.get("name")),
            "description": self._as_text(step.get("description")),
            "related_labels": [self._as_text(item) for item in self._as_list(step.get("related_labels"))],
            "raw": step,
        }

    def _figure_to_text(self, figure: object) -> str:
        if not isinstance(figure, dict):
            return self._as_text(figure)
        title = self._as_text(figure.get("title"))
        description = self._as_text(figure.get("description"))
        labels = "、".join(self._as_text(item) for item in self._as_list(figure.get("labels")) if self._as_text(item))
        parts = []
        if title:
            parts.append(title)
        if description:
            parts.append(description)
        if labels:
            parts.append(f"标注：{labels}")
        return "；".join(parts)

    def _figure_structured(self, page_no: int, title: str, figure_index: int, figure: object) -> dict:
        if not isinstance(figure, dict):
            return {
                "kind": "figure",
                "page_no": page_no,
                "title": title,
                "figure_index": figure_index,
                "raw": self._as_text(figure),
            }
        return {
            "kind": "figure",
            "page_no": page_no,
            "title": title,
            "figure_index": figure_index,
            "figure_title": self._as_text(figure.get("title")),
            "description": self._as_text(figure.get("description")),
            "labels": [self._as_text(item) for item in self._as_list(figure.get("labels"))],
            "raw": figure,
        }

    def _range_text(self, text: str) -> str:
        match = self.RANGE_PATTERN.search(text)
        if not match:
            return ""
        min_value = match.group("min")
        max_value = match.group("max")
        unit = match.group("unit") or ""
        return f"范围解析：最小值={min_value}{unit}，最大值={max_value}{unit}"

    def _range_payload(self, text: str) -> dict:
        match = self.RANGE_PATTERN.search(text)
        if not match:
            return {}
        return {
            "min": match.group("min"),
            "max": match.group("max"),
            "unit": match.group("unit") or "",
        }

    @staticmethod
    def _as_list(value: object) -> list:
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    @staticmethod
    def _as_text(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value).strip()


multimodal_pdf_parser = MultimodalPDFParser()
