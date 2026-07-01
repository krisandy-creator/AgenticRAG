from dataclasses import dataclass


@dataclass
class TextChunk:
    content: str
    page_no: int | None
    chunk_type: str
    metadata: dict


class ChunkingService:
    """按字符窗口切分文档，第一版保持可解释和稳定。"""

    def __init__(self, chunk_size: int = 900, overlap: int = 120):
        self.chunk_size = chunk_size
        self.overlap = overlap

    def split_text(self, text: str, page_no: int | None, chunk_type: str, metadata: dict | None = None) -> list[TextChunk]:
        normalized = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        if not normalized:
            return []

        chunks: list[TextChunk] = []
        start = 0
        while start < len(normalized):
            end = min(start + self.chunk_size, len(normalized))
            content = normalized[start:end].strip()
            if content:
                chunks.append(TextChunk(content=content, page_no=page_no, chunk_type=chunk_type, metadata=metadata or {}))
            if end >= len(normalized):
                break
            start = max(0, end - self.overlap)
        return chunks

