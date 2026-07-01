import json
from datetime import datetime
from typing import Any

from agentchat.domains.documents.repositories import DocumentRepository


class ParseTraceCollector:
    """解析任务 Trace 事件收集器，持久化到 document_parse_job.trace_json。"""

    def __init__(self, job_id: str):
        self.job_id = job_id
        self._events: list[dict[str, Any]] = []

    def record(
        self,
        stage: str,
        status: str,
        message: str,
        *,
        page_no: int | None = None,
        attempt: int | None = None,
        extra: dict | None = None,
    ) -> None:
        event = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "stage": stage,
            "status": status,
            "message": message,
        }
        if page_no is not None:
            event["page_no"] = page_no
        if attempt is not None:
            event["attempt"] = attempt
        if extra:
            event["extra"] = extra
        self._events.append(event)
        DocumentRepository.append_job_trace(self.job_id, event)

    @staticmethod
    def load_events(trace_json: str | None) -> list[dict]:
        if not trace_json:
            return []
        try:
            data = json.loads(trace_json)
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []
