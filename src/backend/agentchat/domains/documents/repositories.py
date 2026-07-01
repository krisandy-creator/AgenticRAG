import json
from datetime import datetime
from typing import Any, Optional

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
    def update_job_progress(
        job_id: str,
        *,
        total_pages: int | None = None,
        parsed_pages: int | None = None,
        indexed_chunks: int | None = None,
        current_stage: str | None = None,
        checkpoint: dict | None = None,
    ) -> None:
        with session_getter() as session:
            job = session.get(DocumentParseJobTable, job_id)
            if not job:
                return
            if total_pages is not None:
                job.total_pages = total_pages
            if parsed_pages is not None:
                job.parsed_pages = parsed_pages
            if indexed_chunks is not None:
                job.indexed_chunks = indexed_chunks
            if current_stage is not None:
                job.current_stage = current_stage
            if checkpoint is not None:
                job.checkpoint_json = json.dumps(checkpoint, ensure_ascii=False)
            session.add(job)
            session.commit()

    @staticmethod
    def append_job_trace(job_id: str, event: dict[str, Any]) -> None:
        with session_getter() as session:
            job = session.get(DocumentParseJobTable, job_id)
            if not job:
                return
            events = DocumentRepository._decode_trace(job.trace_json)
            events.append(event)
            job.trace_json = json.dumps(events, ensure_ascii=False)
            session.add(job)
            session.commit()

    @staticmethod
    def load_job_checkpoint(job: DocumentParseJobTable | None) -> dict:
        if not job or not job.checkpoint_json:
            return {"next_page": 1, "chunk_seq": 0}
        try:
            data = json.loads(job.checkpoint_json)
        except json.JSONDecodeError:
            return {"next_page": 1, "chunk_seq": 0}
        if not isinstance(data, dict):
            return {"next_page": 1, "chunk_seq": 0}
        return {
            "next_page": int(data.get("next_page") or 1),
            "chunk_seq": int(data.get("chunk_seq") or 0),
        }

    @staticmethod
    def replace_pages(document_id: str, pages: list[DocumentPageTable]) -> None:
        with session_getter() as session:
            session.exec(delete(DocumentPageTable).where(DocumentPageTable.document_id == document_id))
            for page in pages:
                session.add(page)
            session.commit()

    @staticmethod
    def append_pages(document_id: str, pages: list[DocumentPageTable]) -> None:
        if not pages:
            return
        with session_getter() as session:
            for page in pages:
                page.document_id = document_id
                session.add(page)
            session.commit()

    @staticmethod
    def clear_parse_artifacts(document_id: str) -> None:
        with session_getter() as session:
            session.exec(delete(DocumentPageTable).where(DocumentPageTable.document_id == document_id))
            session.exec(delete(DocumentChunkTable).where(DocumentChunkTable.document_id == document_id))
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

    @staticmethod
    def _decode_trace(trace_json: str | None) -> list[dict]:
        if not trace_json:
            return []
        try:
            data = json.loads(trace_json)
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []
