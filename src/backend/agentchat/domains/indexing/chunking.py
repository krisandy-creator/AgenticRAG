import re
from dataclasses import dataclass


@dataclass
class TextChunk:
    content: str
    page_no: int | None
    chunk_type: str
    metadata: dict


class ChunkingService:
    """按章节或字符窗口切分文档，保持可解释和稳定。"""

    SECTION_HEADING = re.compile(
        r"^(?:"
        r"#{1,6}\s+.+|"
        r"[一二三四五六七八九十百零]+[、.．]\s*.+|"
        r"[一二三四五六七八九十百零]+\s+[^\s].+|"
        r"第[一二三四五六七八九十百零]+[章节部分条款项]\s*.+|"
        r"(?:\d+\.)+\d*\s+.+|"
        r"[（(][一二三四五六七八九十]+[）)]\s*.+"
        r")$",
        re.MULTILINE,
    )

    def __init__(self, chunk_size: int = 900, overlap: int = 120):
        self.chunk_size = chunk_size
        self.overlap = overlap

    def split_text(self, text: str, page_no: int | None, chunk_type: str, metadata: dict | None = None) -> list[TextChunk]:
        normalized = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        if not normalized:
            return []

        sections = self._split_into_sections(normalized)
        if len(sections) > 1:
            return self._chunks_from_sections(sections, page_no, chunk_type, metadata or {})

        return self._split_by_window(normalized, page_no, chunk_type, metadata or {})

    def _split_into_sections(self, text: str) -> list[tuple[str, str]]:
        lines = text.splitlines()
        sections: list[tuple[str, str]] = []
        current_title = ""
        current_lines: list[str] = []

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if self.SECTION_HEADING.match(stripped):
                if current_lines:
                    sections.append((current_title, "\n".join(current_lines).strip()))
                current_title = self._normalize_section_title(stripped)
                current_lines = [stripped]
                continue
            current_lines.append(stripped)

        if current_lines:
            sections.append((current_title, "\n".join(current_lines).strip()))
        return sections

    @staticmethod
    def _normalize_section_title(line: str) -> str:
        cleaned = re.sub(r"^#{1,6}\s+", "", line.strip())
        cleaned = re.sub(r"^[（(][一二三四五六七八九十]+[）)]\s*", "", cleaned)
        return cleaned.strip()

    def _chunks_from_sections(
        self,
        sections: list[tuple[str, str]],
        page_no: int | None,
        chunk_type: str,
        metadata: dict,
    ) -> list[TextChunk]:
        chunks: list[TextChunk] = []
        for section_title, section_text in sections:
            if not section_text:
                continue
            section_metadata = {
                **metadata,
                "section_title": section_title or None,
                "structured": {
                    **(metadata.get("structured") or {}),
                    **({"section_title": section_title} if section_title else {}),
                },
            }
            if len(section_text) <= self.chunk_size:
                chunks.append(
                    TextChunk(
                        content=section_text,
                        page_no=page_no,
                        chunk_type=chunk_type,
                        metadata=section_metadata,
                    )
                )
                continue
            chunks.extend(self._split_by_window(section_text, page_no, chunk_type, section_metadata))
        return chunks

    def _split_by_window(
        self,
        text: str,
        page_no: int | None,
        chunk_type: str,
        metadata: dict,
    ) -> list[TextChunk]:
        chunks: list[TextChunk] = []
        start = 0
        while start < len(text):
            end = min(start + self.chunk_size, len(text))
            content = text[start:end].strip()
            if content:
                chunks.append(TextChunk(content=content, page_no=page_no, chunk_type=chunk_type, metadata=metadata))
            if end >= len(text):
                break
            start = max(0, end - self.overlap)
        return chunks
