from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import Column, DateTime, Text, text as sa_text
from sqlmodel import Field

from agentchat.database.models.base import SQLModelSerializable


class DocumentTable(SQLModelSerializable, table=True):
    __tablename__ = "document"

    document_id: str = Field(default_factory=lambda: f"doc_{uuid4().hex}", primary_key=True)
    file_name: str = Field(index=True)
    file_type: str = Field(index=True)
    content_hash: str = Field(index=True)
    file_size: int = Field(default=0)
    oss_key: str = Field(index=True)
    permission_level: int = Field(default=10, index=True)
    status: str = Field(default="uploaded", index=True)
    uploaded_by: str = Field(index=True)
    error_message: Optional[str] = Field(default=None, sa_column=Column(Text))
    created_at: Optional[datetime] = Field(
        sa_column=Column(DateTime, nullable=False, index=True, server_default=sa_text("CURRENT_TIMESTAMP"))
    )
    updated_at: Optional[datetime] = Field(
        sa_column=Column(
            DateTime,
            nullable=False,
            server_default=sa_text("CURRENT_TIMESTAMP"),
            onupdate=sa_text("CURRENT_TIMESTAMP"),
        )
    )


class DocumentParseJobTable(SQLModelSerializable, table=True):
    __tablename__ = "document_parse_job"

    job_id: str = Field(default_factory=lambda: f"job_{uuid4().hex}", primary_key=True)
    document_id: str = Field(index=True)
    status: str = Field(default="pending", index=True)
    parser_type: str = Field(index=True)
    error_message: Optional[str] = Field(default=None, sa_column=Column(Text))
    total_pages: Optional[int] = Field(default=None)
    parsed_pages: int = Field(default=0)
    indexed_chunks: int = Field(default=0)
    current_stage: Optional[str] = Field(default=None, max_length=32)
    checkpoint_json: Optional[str] = Field(default=None, sa_column=Column(Text))
    trace_json: Optional[str] = Field(default="[]", sa_column=Column(Text))
    started_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime))
    finished_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime))
    created_at: Optional[datetime] = Field(
        sa_column=Column(DateTime, nullable=False, index=True, server_default=sa_text("CURRENT_TIMESTAMP"))
    )


class DocumentPageTable(SQLModelSerializable, table=True):
    __tablename__ = "document_page"

    page_id: str = Field(default_factory=lambda: f"page_{uuid4().hex}", primary_key=True)
    document_id: str = Field(index=True)
    page_no: int = Field(index=True)
    text: str = Field(sa_column=Column(Text))
    blocks_json: str = Field(default="[]", sa_column=Column(Text))
    created_at: Optional[datetime] = Field(
        sa_column=Column(DateTime, nullable=False, index=True, server_default=sa_text("CURRENT_TIMESTAMP"))
    )
