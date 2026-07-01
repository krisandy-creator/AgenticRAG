from fastapi import APIRouter, Depends
from fastapi_jwt_auth import AuthJWT
from pydantic import BaseModel

from agentchat.api.services.user import get_login_user
from agentchat.domains.identity.services import EnterpriseIdentityService
from agentchat.schema.schemas import resp_200

router = APIRouter(prefix="/auth", tags=["企业认证"])


class LoginRequest(BaseModel):
    user_name: str
    user_password: str


@router.post("/login")
async def login(request: LoginRequest, authorize: AuthJWT = Depends()):
    result = EnterpriseIdentityService.login(request.user_name, request.user_password)
    authorize.set_access_cookies(result["access_token"])
    authorize.set_refresh_cookies(result["refresh_token"])
    return resp_200(data={"access_token": result["access_token"], "user": result["user"]})


@router.get("/me")
async def me(login_user=Depends(get_login_user)):
    return resp_200(data=EnterpriseIdentityService.resolve_user(login_user).model_dump())

