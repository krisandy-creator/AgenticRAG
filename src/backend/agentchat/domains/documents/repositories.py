from datetime import datetime
from typing import Optional

from sqlmodel import delete, select

from agentchat.database.session import session_getter
from agentchat.domains.documents.entities import (
    DocumentPageTable,
    DocumentParseJobTable,
    DocumentTable,
)
from agentchat.domains.indexing.entities import DocumentChunkTable


class DocumentRepository:
    @staticmethod
    def create_document(document: DocumentTable, job: DocumentParseJobTable) -> DocumentTable:
        with session_getter() as session:
            session.add(document)
            session.add(job)
            session.commit()
            session.refresh(document)
            return document

    @staticmethod
    def list_documents(access_level: int, is_admin: bool) -> list[DocumentTable]:
        with session_getter() as session:
            statement = select(DocumentTable).where(DocumentTable.status != "deleted")
            if not is_admin:
                statement = statement.where(DocumentTable.permission_level <= access_level)
            statement = statement.order_by(DocumentTable.created_at.desc())
            return session.exec(statement).all()

    @staticmethod
    def get_document(document_id: str) -> Optional[DocumentTable]:
        with session_getter() as session:
            return session.exec(select(DocumentTable).where(DocumentTable.document_id == document_id)).first()

    @staticmethod
    def get_latest_job(document_id: str) -> Optional[DocumentParseJobTable]:
        with session_getter() as session:
            statement = (
                select(DocumentParseJobTable)
                .where(DocumentParseJobTable.document_id == document_id)
                .order_by(DocumentParseJobTable.created_at.desc())
            )
            return session.exec(statement).first()

    @staticmethod
    def list_pages(document_id: str) -> list[DocumentPageTable]:
        with session_getter() as session:
            statement = (
                select(DocumentPageTable)
                .where(DocumentPageTable.document_id == document_id)
                .order_by(DocumentPageTable.page_no)
            )
            return session.exec(statement).all()

    @staticmethod
    def list_chunks(document_id: str) -> list[DocumentChunkTable]:
        with session_getter() as session:
            statement = (
                select(DocumentChunkTable)
                .where(DocumentChunkTable.document_id == document_id)
                .order_by(DocumentChunkTable.created_at)
            )
            return session.exec(statement).all()

    @staticmethod
    def update_document_status(document_id: str, status: str, error_message: str | None = None) -> None:
        with session_getter() as session:
            document = session.get(DocumentTable, document_id)
            if document:
                document.status = status
                document.error_message = error_message
                session.add(document)
                session.commit()

    @staticmethod
    def update_job_status(job_id: str, status: str, error_message: str | None = None) -> None:
        with session_getter() as session:
            job = session.get(DocumentParseJobTable, job_id)
            if job:
                job.status = status
                job.error_message = error_message
                if status == "running":
                    job.started_at = datetime.now()
                if status in {"success", "failed"}:
                    job.finished_at = datetime.now()
                session.add(job)
                session.commit()

    @staticmethod
    def replace_pages(document_id: str, pages: list[DocumentPageTable]) -> None:
        with session_getter() as session:
            session.exec(delete(DocumentPageTable).where(DocumentPageTable.document_id == document_id))
            for page in pages:
                session.add(page)
            session.commit()

    @staticmethod
    def delete_document(document_id: str) -> None:
        with session_getter() as session:
            session.exec(delete(DocumentChunkTable).where(DocumentChunkTable.document_id == document_id))
            session.exec(delete(DocumentPageTable).where(DocumentPageTable.document_id == document_id))
            session.exec(delete(DocumentParseJobTable).where(DocumentParseJobTable.document_id == document_id))
            document = session.get(DocumentTable, document_id)
            if document:
                document.status = "deleted"
                session.add(document)
            session.commit()
