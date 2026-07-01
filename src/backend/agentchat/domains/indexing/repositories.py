from sqlmodel import delete, select

from agentchat.database.session import session_getter
from agentchat.domains.documents.entities import DocumentTable
from agentchat.domains.indexing.entities import DocumentChunkTable


class ChunkRepository:
    @staticmethod
    def replace_chunks(document_id: str, chunks: list[DocumentChunkTable]) -> None:
        with session_getter() as session:
            session.exec(delete(DocumentChunkTable).where(DocumentChunkTable.document_id == document_id))
            for chunk in chunks:
                session.add(chunk)
            session.commit()

    @staticmethod
    def append_chunks(document_id: str, chunks: list[DocumentChunkTable]) -> None:
        if not chunks:
            return
        with session_getter() as session:
            for chunk in chunks:
                chunk.document_id = document_id
                session.add(chunk)
            session.commit()

    @staticmethod
    def list_searchable_chunks(access_level: int, limit: int = 1000) -> list[tuple[DocumentChunkTable, DocumentTable]]:
        with session_getter() as session:
            statement = (
                select(DocumentChunkTable, DocumentTable)
                .where(DocumentChunkTable.document_id == DocumentTable.document_id)
                .where(DocumentTable.status == "ready")
                .where(DocumentChunkTable.permission_level <= access_level)
                .limit(limit)
            )
            return session.exec(statement).all()

    @staticmethod
    def list_searchable_chunks_by_vector_ids(
        vector_ids: list[str],
        access_level: int,
    ) -> list[tuple[DocumentChunkTable, DocumentTable]]:
        if not vector_ids:
            return []

        order = {vector_id: index for index, vector_id in enumerate(vector_ids)}
        with session_getter() as session:
            statement = (
                select(DocumentChunkTable, DocumentTable)
                .where(DocumentChunkTable.document_id == DocumentTable.document_id)
                .where(DocumentTable.status == "ready")
                .where(DocumentChunkTable.permission_level <= access_level)
                .where(DocumentChunkTable.vector_id.in_(vector_ids))
            )
            rows = session.exec(statement).all()
            return sorted(rows, key=lambda row: order.get(row[0].vector_id, len(order)))
