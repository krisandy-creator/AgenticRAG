from datetime import datetime

from sqlmodel import select

from agentchat.database.session import session_getter
from agentchat.domains.observability.entities import TraceEventTable, TraceTable


class TraceRepository:
    @staticmethod
    def create_trace(trace: TraceTable) -> TraceTable:
        with session_getter() as session:
            session.add(trace)
            session.commit()
            session.refresh(trace)
            return trace

    @staticmethod
    def append_event(event: TraceEventTable) -> TraceEventTable:
        with session_getter() as session:
            session.add(event)
            session.commit()
            session.refresh(event)
            return event

    @staticmethod
    def finish_trace(trace_id: str, status: str) -> None:
        with session_getter() as session:
            trace = session.get(TraceTable, trace_id)
            if trace:
                trace.status = status
                trace.finished_at = datetime.now()
                session.add(trace)
                session.commit()

    @staticmethod
    def list_traces(user_id: str, is_admin: bool) -> list[TraceTable]:
        with session_getter() as session:
            statement = select(TraceTable)
            if not is_admin:
                statement = statement.where(TraceTable.user_id == user_id)
            statement = statement.order_by(TraceTable.created_at.desc())
            return session.exec(statement).all()

    @staticmethod
    def get_trace(trace_id: str) -> TraceTable | None:
        with session_getter() as session:
            return session.get(TraceTable, trace_id)

    @staticmethod
    def list_events(trace_id: str) -> list[TraceEventTable]:
        with session_getter() as session:
            statement = select(TraceEventTable).where(TraceEventTable.trace_id == trace_id).order_by(TraceEventTable.seq)
            return session.exec(statement).all()

