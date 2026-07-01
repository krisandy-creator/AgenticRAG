from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import Column, DateTime, Text, text
from sqlmodel import Field

from agentchat.database.models.base import SQLModelSerializable


class TraceTable(SQLModelSerializable, table=True):
    __tablename__ = "trace"

    trace_id: str = Field(default_factory=lambda: f"trace_{uuid4().hex}", primary_key=True)
    session_id: str = Field(index=True)
    user_id: str = Field(index=True)
    question: str = Field(sa_column=Column(Text))
    mode: str = Field(default="normal", index=True)
    status: str = Field(default="running", index=True)
    created_at: Optional[datetime] = Field(
        sa_column=Column(DateTime, nullable=False, index=True, server_default=text("CURRENT_TIMESTAMP"))
    )
    finished_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime))


class TraceEventTable(SQLModelSerializable, table=True):
    __tablename__ = "trace_event"

    event_id: str = Field(default_factory=lambda: f"event_{uuid4().hex}", primary_key=True)
    trace_id: str = Field(index=True)
    seq: int = Field(index=True)
    event_type: str = Field(index=True)
    payload_json: str = Field(default="{}", sa_column=Column(Text))
    created_at: Optional[datetime] = Field(
        sa_column=Column(DateTime, nullable=False, index=True, server_default=text("CURRENT_TIMESTAMP"))
    )

