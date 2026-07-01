from fastapi import APIRouter, Depends, File, Form, UploadFile

from agentchat.api.services.user import get_login_user
from agentchat.domains.documents.services import DocumentService
from agentchat.domains.identity.services import EnterpriseIdentityService
from agentchat.schema.schemas import resp_200

router = APIRouter(prefix="/documents", tags=["企业文档"])


@router.get("")
async def list_documents(login_user=Depends(get_login_user)):
    user = EnterpriseIdentityService.resolve_user(login_user)
    return resp_200(data=DocumentService().list_documents(user.access_level, user.is_admin))


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    permission_level: int = Form(10),
    login_user=Depends(get_login_user),
):
    user = EnterpriseIdentityService.resolve_user(login_user)
    document = await DocumentService().upload_document(file, permission_level, user.user_id)
    return resp_200(data=document.to_dict())


@router.get("/{document_id}")
async def get_document(document_id: str, login_user=Depends(get_login_user)):
    user = EnterpriseIdentityService.resolve_user(login_user)
    document = DocumentService().get_document_for_user(document_id, user.access_level, user.is_admin)
    return resp_200(data=document.to_dict())


@router.delete("/{document_id}")
async def delete_document(document_id: str, login_user=Depends(get_login_user)):
    user = EnterpriseIdentityService.resolve_user(login_user)
    DocumentService().delete_document(document_id, user.is_admin)
    return resp_200()


@router.get("/{document_id}/download-url")
async def download_url(document_id: str, login_user=Depends(get_login_user)):
    user = EnterpriseIdentityService.resolve_user(login_user)
    url = await DocumentService().get_download_url(document_id, user.access_level, user.is_admin)
    return resp_200(data={"url": url})


@router.get("/{document_id}/parse-job")
async def parse_job(document_id: str, login_user=Depends(get_login_user)):
    user = EnterpriseIdentityService.resolve_user(login_user)
    return resp_200(data=DocumentService().get_parse_job(document_id, user.access_level, user.is_admin))

