from fastapi import APIRouter
from agentchat.api.v1 import auth, chat, documents, traces

router = APIRouter(prefix="/api/v1")

router.include_router(auth.router)
router.include_router(documents.router)
router.include_router(chat.router)
router.include_router(traces.router)
