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
    def list_chunks_by_ids(chunk_ids: list[str]) -> list[tuple[DocumentChunkTable, DocumentTable]]:
        if not chunk_ids:
            return []

        order = {chunk_id: index for index, chunk_id in enumerate(chunk_ids)}
        with session_getter() as session:
            statement = (
                select(DocumentChunkTable, DocumentTable)
                .where(DocumentChunkTable.document_id == DocumentTable.document_id)
                .where(DocumentTable.status == "ready")
                .where(DocumentChunkTable.status == "active")
                .where(DocumentChunkTable.chunk_id.in_(chunk_ids))
            )
            rows = session.exec(statement).all()
            return sorted(rows, key=lambda row: order.get(row[0].chunk_id, len(order)))
