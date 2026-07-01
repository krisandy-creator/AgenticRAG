from urllib.parse import urlparse

import yaml
from arq import create_pool
from arq.connections import RedisSettings
from loguru import logger

from agentchat.services.rag.doc_parser.pdf_config import load_parse_worker_config
from agentchat.settings import app_settings


def _load_redis_endpoint() -> str:
    endpoint = (app_settings.redis or {}).get("endpoint") if app_settings.redis else None
    if endpoint:
        return endpoint
    for path in ("agentchat/config.local.yaml", "agentchat/config.yaml"):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle) or {}
            endpoint = (data.get("redis") or {}).get("endpoint")
            if endpoint:
                return endpoint
        except FileNotFoundError:
            continue
    return "redis://localhost:6379/0"


def _redis_settings() -> RedisSettings:
    endpoint = _load_redis_endpoint()
    parsed = urlparse(endpoint)
    database = 0
    if parsed.path and parsed.path != "/":
        database = int(parsed.path.lstrip("/") or 0)
    host = parsed.hostname or "localhost"
    if host in {"localhost", "::1"}:
        host = "127.0.0.1"
    return RedisSettings(
        host=host,
        port=parsed.port or 6379,
        password=parsed.password,
        database=database,
        conn_timeout=5,
    )


async def enqueue_document_parse(document_id: str) -> str | None:
    worker_config = load_parse_worker_config()
    redis = await create_pool(_redis_settings())
    try:
        job = await redis.enqueue_job(
            "parse_document_job",
            document_id,
            _queue_name=worker_config.arq_queue_name,
        )
        job_id = job.job_id if job else None
        logger.info("文档解析任务已入队 document_id={} arq_job_id={}", document_id, job_id)
        return job_id
    finally:
        await redis.aclose()
