from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from agentchat.api.services.user import get_login_user
from agentchat.domains.identity.services import EnterpriseIdentityService
from agentchat.domains.rag.repositories import ChatRepository
from agentchat.domains.rag.workflow import RagWorkflow, sse_format
from agentchat.schema.schemas import resp_200

router = APIRouter(prefix="/chat", tags=["企业问答"])


class CreateSessionRequest(BaseModel):
    title: str | None = None


class ChatStreamRequest(BaseModel):
    session_id: str
    question: str
    mode: str = "normal"


@router.post("/sessions")
async def create_session(request: CreateSessionRequest | None = None, login_user=Depends(get_login_user)):
    user = EnterpriseIdentityService.resolve_user(login_user)
    session = ChatRepository.create_session(user.user_id, request.title if request else None)
    return resp_200(data=session.to_dict())


@router.get("/sessions")
async def list_sessions(login_user=Depends(get_login_user)):
    user = EnterpriseIdentityService.resolve_user(login_user)
    return resp_200(data=[session.to_dict() for session in ChatRepository.list_sessions(user.user_id)])


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, login_user=Depends(get_login_user)):
    user = EnterpriseIdentityService.resolve_user(login_user)
    session = ChatRepository.get_session(session_id)
    if not session or session.user_id != user.user_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    ChatRepository.delete_session(session_id)
    return resp_200()


@router.get("/sessions/{session_id}/messages")
async def list_messages(session_id: str, login_user=Depends(get_login_user)):
    user = EnterpriseIdentityService.resolve_user(login_user)
    session = ChatRepository.get_session(session_id)
    if not session or session.user_id != user.user_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    return resp_200(data=[message.to_dict() for message in ChatRepository.list_messages(session_id)])


@router.post("/stream")
async def stream_chat(request: ChatStreamRequest, login_user=Depends(get_login_user)):
    user = EnterpriseIdentityService.resolve_user(login_user)
    session = ChatRepository.get_session(request.session_id)
    if not session or session.user_id != user.user_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    if request.mode not in {"normal", "deep_analysis"}:
        raise HTTPException(status_code=400, detail="mode 仅支持 normal 或 deep_analysis")

    async def event_stream():
        workflow = RagWorkflow()
        async for event in workflow.stream(request.session_id, user, request.question, request.mode):
            yield sse_format(event)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
