from typing import Optional, Union
from pydantic import BaseModel, Field, field_validator

class KnowledgeCreateRequest(BaseModel):
    knowledge_name: str = Field(description="知识库名称", min_length=2, max_length=10)
    knowledge_desc: Optional[str] = Field(default=None, description="知识库的描述")

    @field_validator('knowledge_desc')
    @classmethod
    def validate_desc_length(cls, v: Optional[str]) -> Optional[str]:
        # 如果提供了描述且不为None，验证长度
        if v is not None and len(v) > 0:
            if len(v) < 10 or len(v) > 200:
                raise ValueError("knowledge_desc 长度必须在 10-200 之间")
        return v

class KnowledgeUpdateRequest(BaseModel):
    knowledge_id: str = Field(description="知识库ID")
    knowledge_name: Optional[str] = Field(default=None, description="知识库名称")
    knowledge_desc: Optional[str] = Field(default=None, description="知识库的描述")

    @field_validator('knowledge_name')
    @classmethod
    def validate_name_length(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            if len(v) < 2 or len(v) > 10:
                raise ValueError("knowledge_name 长度必须在 2-10 之间")
        return v

    @field_validator('knowledge_desc')
    @classmethod
    def validate_desc_length_update(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v) > 0:
            if len(v) < 10 or len(v) > 200:
                raise ValueError("knowledge_desc 长度必须在 10-200 之间")
        return v