from agentchat.settings import initialize_app_settings, load_app_settings_sync

load_app_settings_sync()

from agentchat.services.rag.doc_parser.pdf_config import load_parse_worker_config
from agentchat.workers.document_parse_worker import parse_document_task
from agentchat.workers.parse_queue import _redis_settings


async def startup(ctx):
    await initialize_app_settings()
    ctx["settings_loaded"] = True


async def shutdown(ctx):
    ctx.clear()


async def parse_document_job(ctx, document_id: str):
    await parse_document_task(document_id)


_worker_config = load_parse_worker_config()


class WorkerSettings:
    functions = [parse_document_job]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = _redis_settings()
    queue_name = _worker_config.arq_queue_name
    max_jobs = _worker_config.max_concurrent_documents
    job_timeout = _worker_config.job_timeout_seconds
