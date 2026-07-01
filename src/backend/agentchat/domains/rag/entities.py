from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import Column, DateTime, Text, text
from sqlmodel import Field

from agentchat.database.models.base import SQLModelSerializable


class ChatSessionTable(SQLModelSerializable, table=True):
    __tablename__ = "chat_session"

    session_id: str = Field(default_factory=lambda: f"s_{uuid4().hex}", primary_key=True)
    user_id: str = Field(index=True)
    title: str = Field(default="新的知识库问答")
    created_at: Optional[datetime] = Field(
        sa_column=Column(DateTime, nullable=False, index=True, server_default=text("CURRENT_TIMESTAMP"))
    )
    updated_at: Optional[datetime] = Field(
        sa_column=Column(
            DateTime,
            nullable=False,
            server_default=text("CURRENT_TIMESTAMP"),
            onupdate=text("CURRENT_TIMESTAMP"),
        )
    )


class ChatMessageTable(SQLModelSerializable, table=True):
    __tablename__ = "chat_message"

    message_id: str = Field(default_factory=lambda: f"msg_{uuid4().hex}", primary_key=True)
    session_id: str = Field(index=True)
    role: str = Field(index=True)
    content: str = Field(sa_column=Column(Text))
    trace_id: Optional[str] = Field(default=None, index=True)
    created_at: Optional[datetime] = Field(
        sa_column=Column(DateTime, nullable=False, index=True, server_default=text("CURRENT_TIMESTAMP"))
    )

