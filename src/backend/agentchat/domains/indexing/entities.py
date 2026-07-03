from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field as PydanticField
from sqlalchemy import Column, DateTime, Text, text
from sqlmodel import Field

from agentchat.database.models.base import SQLModelSerializable


class DocumentChunkTable(SQLModelSerializable, table=True):
    __tablename__ = "document_chunk"

    chunk_id: str = Field(default_factory=lambda: f"chunk_{uuid4().hex}", primary_key=True)
    document_id: str = Field(index=True)
    page_no: Optional[int] = Field(default=None, index=True)
    chunk_type: str = Field(default="text", index=True)
    content: str = Field(sa_column=Column(Text))
    content_hash: str = Field(default="", index=True)
    vector_id: str = Field(index=True)
    permission_level: int = Field(index=True)
    index_version: int = Field(default=1, index=True)
    status: str = Field(default="active", index=True)
    metadata_json: str = Field(default="{}", sa_column=Column(Text))
    created_at: Optional[datetime] = Field(
        sa_column=Column(DateTime, nullable=False, index=True, server_default=text("CURRENT_TIMESTAMP"))
    )


@dataclass
class RetrievalCandidate:
    chunk_id: str
    document_id: str = ""
    vector_id: str = ""
    sparse_score: float = 0.0
    sparse_rank: int = 0
    dense_score: float = 0.0
    dense_rank: int = 0
    rrf_score: float = 0.0
    fusion_rank: int | None = None
    content: str = ""


class SearchHit(BaseModel):
    chunk_id: str
    document_id: str
    file_name: str
    file_type: str
    content: str
    page_no: int | None = None
    chunk_type: str
    permission_level: int
    score: float
    metadata: dict = PydanticField(default_factory=dict)
    score_breakdown: dict[str, float] = PydanticField(default_factory=dict)
    rank_sources: list[str] = PydanticField(default_factory=list)
    rank: int | None = None
