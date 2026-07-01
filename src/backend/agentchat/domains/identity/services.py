import json
from typing import Any

from fastapi import HTTPException

from agentchat.api.services.user import UserPayload, UserService, get_user_jwt
from agentchat.database.dao.user import UserDao
from agentchat.database.models.role import AdminRole
from agentchat.domains.identity.entities import EnterpriseUser


class EnterpriseIdentityService:
    """企业知识库身份服务，负责把旧账号体系映射为角色等级。"""

    @staticmethod
    def _normalize_role_ids(user_payload: UserPayload) -> list[str]:
        if user_payload.user_role == "admin":
            return [AdminRole]
        if isinstance(user_payload.user_role, list):
            return [str(role_id) for role_id in user_payload.user_role]
        if user_payload.user_role:
            return [str(user_payload.user_role)]
        return []

    @classmethod
    def resolve_user(cls, user_payload: UserPayload) -> EnterpriseUser:
        role_ids = cls._normalize_role_ids(user_payload)
        is_admin = user_payload.is_admin()
        access_level = 100 if is_admin else 10
        role_name = "管理员" if is_admin else "普通员工"

        if not is_admin:
            numeric_roles = [int(role_id) for role_id in role_ids if role_id.isdigit()]
            if numeric_roles:
                access_level = max(10, max(numeric_roles) * 10)

        return EnterpriseUser(
            user_id=str(user_payload.user_id),
            user_name=user_payload.user_name or "未知用户",
            role_name=role_name,
            access_level=access_level,
            is_admin=is_admin,
        )

    @classmethod
    def login(cls, user_name: str, user_password: str) -> dict[str, Any]:
        db_user = UserDao.get_user_by_username(user_name)
        if not db_user or not UserService.verify_password(user_password, db_user.user_password):
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        if db_user.delete:
            raise HTTPException(status_code=403, detail="该账号已被禁用，请联系管理员")

        access_token, refresh_token, role = get_user_jwt(db_user)
        payload = UserPayload(user_id=db_user.user_id, user_name=db_user.user_name, role=role)
        profile = cls.resolve_user(payload)
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "user": profile.model_dump(),
            "legacy_subject": json.dumps({"user_id": db_user.user_id, "user_name": db_user.user_name, "role": role}),
        }

