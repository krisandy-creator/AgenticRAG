import json

from agentchat.domains.observability.entities import TraceEventTable, TraceTable
from agentchat.domains.observability.event_schema import TraceEvent
from agentchat.domains.observability.repositories import TraceRepository


class TraceService:
    @staticmethod
    def create_trace(session_id: str, user_id: str, question: str, mode: str) -> TraceTable:
        return TraceRepository.create_trace(
            TraceTable(session_id=session_id, user_id=user_id, question=question, mode=mode, status="running")
        )

    @staticmethod
    def emit(trace_id: str, seq: int, event_type: str, payload: dict) -> TraceEvent:
        event = TraceEventTable(
            trace_id=trace_id,
            seq=seq,
            event_type=event_type,
            payload_json=json.dumps(payload, ensure_ascii=False),
        )
        saved = TraceRepository.append_event(event)
        return TraceEvent(trace_id=trace_id, seq=saved.seq, event_type=saved.event_type, payload=payload)

    @staticmethod
    def finish(trace_id: str, status: str = "success") -> None:
        TraceRepository.finish_trace(trace_id, status)

    @staticmethod
    def list_traces(user_id: str, is_admin: bool) -> list[dict]:
        return [trace.to_dict() for trace in TraceRepository.list_traces(user_id, is_admin)]

    @staticmethod
    def get_trace(trace_id: str) -> dict | None:
        trace = TraceRepository.get_trace(trace_id)
        return trace.to_dict() if trace else None

    @staticmethod
    def list_events(trace_id: str) -> list[dict]:
        events = []
        for event in TraceRepository.list_events(trace_id):
            item = event.to_dict()
            try:
                item["payload"] = json.loads(event.payload_json or "{}")
            except json.JSONDecodeError:
                item["payload"] = {}
            events.append(item)
        return events

