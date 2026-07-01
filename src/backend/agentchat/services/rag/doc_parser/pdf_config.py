from dataclasses import dataclass

import yaml

from agentchat.settings import app_settings


@dataclass(frozen=True)
class PDFParseConfig:
    enabled: bool = True
    dpi: int = 180
    max_pages: int = 500
    page_batch_size: int = 20
    vision_max_concurrency_per_doc: int = 4
    vision_max_retries: int = 3
    vision_global_limit: int = 10
    vision_timeout_seconds: int = 120
    text_layer_min_chars: int = 80
    blank_page_max_nonwhite_ratio: float = 0.005
    embedding_batch_size: int = 32
    embedding_api_batch_size: int = 10
    embedding_max_concurrency: int = 5


@dataclass(frozen=True)
class ParseWorkerConfig:
    max_concurrent_documents: int = 2
    arq_queue_name: str = "rag:parse"
    job_timeout_seconds: int = 7200


def _rag_dict() -> dict:
    rag = app_settings.rag
    if rag:
        if isinstance(rag, dict):
            return rag
        if hasattr(rag, "model_dump"):
            return rag.model_dump()
        return rag.dict()

    for path in ("agentchat/config.local.yaml", "agentchat/config.yaml"):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle) or {}
            rag_section = data.get("rag")
            if isinstance(rag_section, dict):
                return rag_section
        except FileNotFoundError:
            continue
    return {}


def load_pdf_parse_config() -> PDFParseConfig:
    rag = _rag_dict()
    raw = rag.get("multimodal_parse") or {}
    if not isinstance(raw, dict):
        raw = {}
    return PDFParseConfig(
        enabled=bool(raw.get("enabled", True)),
        dpi=int(raw.get("dpi", 180)),
        max_pages=int(raw.get("max_pages", 500)),
        page_batch_size=int(raw.get("page_batch_size", 20)),
        vision_max_concurrency_per_doc=int(raw.get("vision_max_concurrency_per_doc", 4)),
        vision_max_retries=int(raw.get("vision_max_retries", 3)),
        vision_global_limit=int(raw.get("vision_global_limit", 10)),
        vision_timeout_seconds=int(raw.get("vision_timeout_seconds", 120)),
        text_layer_min_chars=int(raw.get("text_layer_min_chars", 80)),
        blank_page_max_nonwhite_ratio=float(raw.get("blank_page_max_nonwhite_ratio", 0.005)),
        embedding_batch_size=int(raw.get("embedding_batch_size", 32)),
        embedding_api_batch_size=int(raw.get("embedding_api_batch_size", 10)),
        embedding_max_concurrency=int(raw.get("embedding_max_concurrency", 5)),
    )


def load_parse_worker_config() -> ParseWorkerConfig:
    rag = _rag_dict()
    raw = rag.get("parse_worker") or {}
    if not isinstance(raw, dict):
        raw = {}
    return ParseWorkerConfig(
        max_concurrent_documents=int(raw.get("max_concurrent_documents", 2)),
        arq_queue_name=str(raw.get("arq_queue_name", "rag:parse")),
        job_timeout_seconds=int(raw.get("job_timeout_seconds", 7200)),
    )
