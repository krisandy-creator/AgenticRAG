from pydantic import BaseModel


class EnterpriseUser(BaseModel):
    """当前企业知识库用户上下文。"""

    user_id: str
    user_name: str
    role_name: str
    access_level: int
    is_admin: bool

