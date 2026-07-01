from fastapi import APIRouter, Depends, HTTPException

from agentchat.api.services.user import get_login_user
from agentchat.domains.identity.services import EnterpriseIdentityService
from agentchat.domains.observability.services import TraceService
from agentchat.schema.schemas import resp_200

router = APIRouter(prefix="/traces", tags=["企业 Trace"])


@router.get("")
async def list_traces(login_user=Depends(get_login_user)):
    user = EnterpriseIdentityService.resolve_user(login_user)
    return resp_200(data=TraceService.list_traces(user.user_id, user.is_admin))


@router.get("/{trace_id}")
async def get_trace(trace_id: str, login_user=Depends(get_login_user)):
    user = EnterpriseIdentityService.resolve_user(login_user)
    trace = TraceService.get_trace(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace 不存在")
    if not user.is_admin and trace["user_id"] != user.user_id:
        raise HTTPException(status_code=403, detail="无权查看该 Trace")
    return resp_200(data=trace)


@router.get("/{trace_id}/events")
async def list_events(trace_id: str, login_user=Depends(get_login_user)):
    user = EnterpriseIdentityService.resolve_user(login_user)
    trace = TraceService.get_trace(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace 不存在")
    if not user.is_admin and trace["user_id"] != user.user_id:
        raise HTTPException(status_code=403, detail="无权查看该 Trace")
    return resp_200(data=TraceService.list_events(trace_id))

