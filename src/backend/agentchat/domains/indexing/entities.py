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
    vector_id: str = Field(index=True)
    permission_level: int = Field(index=True)
    metadata_json: str = Field(default="{}", sa_column=Column(Text))
    created_at: Optional[datetime] = Field(
        sa_column=Column(DateTime, nullable=False, index=True, server_default=text("CURRENT_TIMESTAMP"))
    )


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
