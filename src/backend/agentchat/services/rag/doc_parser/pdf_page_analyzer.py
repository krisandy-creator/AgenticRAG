"""PDF 页分析：页数、空白页检测、文本层抽取。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PageAnalysis:
    page_no: int
    is_blank: bool
    text_layer: str
    use_text_layer: bool
    nonwhite_ratio: float


def get_page_count(data: bytes) -> int:
    import fitz

    with fitz.open(stream=data, filetype="pdf") as document:
        return document.page_count


def analyze_page(page, page_no: int, *, text_layer_min_chars: int, blank_max_nonwhite_ratio: float) -> PageAnalysis:
    text_layer = (page.get_text("text") or "").strip()
    nonwhite_ratio = _estimate_nonwhite_ratio(page)
    is_blank = _is_blank(text_layer, nonwhite_ratio, blank_max_nonwhite_ratio)
    use_text_layer = (not is_blank) and len(text_layer) >= text_layer_min_chars
    return PageAnalysis(
        page_no=page_no,
        is_blank=is_blank,
        text_layer=text_layer,
        use_text_layer=use_text_layer,
        nonwhite_ratio=nonwhite_ratio,
    )


def _is_blank(text_layer: str, nonwhite_ratio: float, blank_max_nonwhite_ratio: float) -> bool:
    if len(text_layer) >= 10:
        return False
    return nonwhite_ratio <= blank_max_nonwhite_ratio


def _estimate_nonwhite_ratio(page) -> float:
    import fitz

    matrix = fitz.Matrix(0.25, 0.25)
    pixmap = page.get_pixmap(matrix=matrix, alpha=False)
    samples = pixmap.samples
    if not samples:
        return 0.0

    step = 3 if pixmap.n >= 3 else 1
    total = 0
    nonwhite = 0
    for index in range(0, len(samples), step * 4):
        if index + 2 >= len(samples):
            break
        red = samples[index]
        green = samples[index + 1]
        blue = samples[index + 2]
        total += 1
        if red < 245 or green < 245 or blue < 245:
            nonwhite += 1
    return nonwhite / total if total else 0.0
