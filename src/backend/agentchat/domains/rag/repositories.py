from sqlmodel import delete, select

from agentchat.database.session import session_getter
from agentchat.domains.observability.entities import TraceEventTable, TraceTable
from agentchat.domains.rag.entities import ChatMessageTable, ChatSessionTable


class ChatRepository:
    @staticmethod
    def create_session(user_id: str, title: str | None = None) -> ChatSessionTable:
        session_title = title or "新的知识库问答"
        with session_getter() as session:
            chat_session = ChatSessionTable(user_id=user_id, title=session_title)
            session.add(chat_session)
            session.commit()
            session.refresh(chat_session)
            return chat_session

    @staticmethod
    def list_sessions(user_id: str) -> list[ChatSessionTable]:
        with session_getter() as session:
            statement = select(ChatSessionTable).where(ChatSessionTable.user_id == user_id).order_by(ChatSessionTable.updated_at.desc())
            return session.exec(statement).all()

    @staticmethod
    def get_session(session_id: str) -> ChatSessionTable | None:
        with session_getter() as session:
            return session.get(ChatSessionTable, session_id)

    @staticmethod
    def delete_session(session_id: str) -> None:
        with session_getter() as session:
            trace_ids = session.exec(select(TraceTable.trace_id).where(TraceTable.session_id == session_id)).all()
            if trace_ids:
                session.exec(delete(TraceEventTable).where(TraceEventTable.trace_id.in_(trace_ids)))
                session.exec(delete(TraceTable).where(TraceTable.trace_id.in_(trace_ids)))
            session.exec(delete(ChatMessageTable).where(ChatMessageTable.session_id == session_id))
            session.exec(delete(ChatSessionTable).where(ChatSessionTable.session_id == session_id))
            session.commit()

    @staticmethod
    def add_message(session_id: str, role: str, content: str, trace_id: str | None = None) -> ChatMessageTable:
        with session_getter() as session:
            message = ChatMessageTable(session_id=session_id, role=role, content=content, trace_id=trace_id)
            session.add(message)
            chat_session = session.get(ChatSessionTable, session_id)
            if chat_session and role == "user":
                chat_session.title = content[:32] or chat_session.title
                session.add(chat_session)
            session.commit()
            session.refresh(message)
            return message

    @staticmethod
    def list_messages(session_id: str) -> list[ChatMessageTable]:
        with session_getter() as session:
            statement = select(ChatMessageTable).where(ChatMessageTable.session_id == session_id).order_by(ChatMessageTable.created_at)
            return session.exec(statement).all()
