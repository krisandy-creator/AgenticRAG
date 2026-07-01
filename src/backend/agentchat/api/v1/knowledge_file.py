import os
from urllib.parse import urlparse, unquote
from fastapi import FastAPI, APIRouter, Body, Depends, Query

from agentchat.services.storage import storage_client
from agentchat.api.services.knowledge_file import KnowledgeFileService
from agentchat.api.services.knowledge import KnowledgeService
from agentchat.api.services.user import get_login_user, UserPayload
from agentchat.schema.schemas import UnifiedResponseModel, resp_200, resp_500
from agentchat.utils.file_utils import get_save_tempfile, normalize_object_storage_value

router = APIRouter(tags=["Knowledge-File"])


@router.post('/knowledge_file/create', response_model=UnifiedResponseModel)
async def upload_file(
    knowledge_id: str = Body(..., description="知识库的ID"),
    file_url: str = Body(..., description="文件上传后返回的URL"),
    login_user: UserPayload = Depends(get_login_user)
):
    try:
        # 先解码 URL（防止双重编码）
        file_url = unquote(file_url)

        # 从预签名 URL 中提取 object key
        if file_url.startswith("http://") or file_url.startswith("https://"):
            # 先去除查询参数
            if "?" in file_url:
                file_url = file_url.split("?")[0]

            normalized = normalize_object_storage_value(file_url)
            if normalized != file_url:
                # 成功提取出 object key（已经是相对路径，不含 bucket name）
                object_key = normalized
            else:
                # 未能提取，尝试从路径中提取
                parsed = urlparse(file_url)
                object_key = parsed.path.lstrip('/')
                # 移除 bucket name（如果存在）
                if object_key.startswith("agentchat/"):
                    object_key = object_key[len("agentchat/"):]
        else:
            object_key = file_url

        # 确保 object_key 不包含 bucket name 前缀
        if object_key.startswith("agentchat/"):
            object_key = object_key[len("agentchat/"):]

        # 从 object_key 中提取文件名
        file_name = object_key.split("/")[-1]

        # 获取本地临时文件路径
        local_file_path = get_save_tempfile(file_name)

        # 使用 object_key 下载文件（MinIO 客户端会自动添加 bucket name）
        storage_client.download_file(object_key, local_file_path)

        # 获得文件的字节数
        file_size_bytes = os.path.getsize(local_file_path)

        # 处理文件名：去掉 UUID 后缀
        if '.' in file_name:
            name_part, ext_part = file_name.rsplit('.', 1)
            parts = name_part.split("_")
            # 去掉最后一部分（UUID）
            if len(parts) > 1:
                file_name = "_".join(parts[:-1]) + f".{ext_part}"
            else:
                file_name = f"{name_part}.{ext_part}"

        # 构造基础 URL（不带预签名参数），存入数据库
        from agentchat.settings import app_settings
        base_url = app_settings.storage.minio.base_url
        # 确保 base_url 不以斜杠结尾，然后添加 object_key
        base_url = base_url.rstrip('/')
        # 直接拼接，因为 base_url 配置中已经包含了 /agentchat
        oss_base_url = f"{base_url}/{object_key}"

        await KnowledgeFileService.create_knowledge_file(
            file_name=file_name,
            file_path=local_file_path,
            knowledge_id=knowledge_id,
            user_id=login_user.user_id,
            oss_url=oss_base_url,  # 存储基础 URL
            file_size_bytes=file_size_bytes
        )
        return resp_200()
    except Exception as err:
        return resp_500(message=str(err))


@router.get('/knowledge_file/select', response_model=UnifiedResponseModel)
async def select_knowledge_file(
    knowledge_id: str = Query(...),
    login_user: UserPayload = Depends(get_login_user)
):
    try:
        # 验证用户权限
        await KnowledgeService.verify_user_permission(knowledge_id, login_user.user_id)

        results = await KnowledgeFileService.get_knowledge_file(knowledge_id)

        # 动态为每个文件添加预签名 URL
        from agentchat.settings import app_settings
        base_url = app_settings.storage.minio.base_url.rstrip('/')

        for file in results:
            if file.get('oss_url'):
                oss_url = file['oss_url']
                # 如果存储的是基础 URL（不含查询参数），则转换为预签名 URL
                if oss_url.startswith(base_url) and '?' not in oss_url:
                    # 从基础 URL 中提取 object key（移除 /agentchat/ 前缀）
                    object_key = oss_url[len(base_url):].lstrip('/')
                    if object_key.startswith("agentchat/"):
                        object_key = object_key[len("agentchat/"):]
                    # 生成预签名 URL
                    file['oss_url'] = storage_client.sign_url_for_get(object_key)

        return resp_200(data=results)
    except Exception as err:
        return resp_500(message=str(err))


@router.delete('/knowledge_file/delete', response_model=UnifiedResponseModel)
async def delete_knowledge_file(
    knowledge_file_id: str = Body(..., embed=True),
    login_user: UserPayload = Depends(get_login_user)
):
    try:
        # 验证用户权限
        await KnowledgeFileService.verify_user_permission(knowledge_file_id, login_user.user_id)

        await KnowledgeFileService.delete_knowledge_file(knowledge_file_id)
        return resp_200()
    except Exception as err:
        return resp_500(message=str(err))

@router.get("/knowledge_file/status", response_model=UnifiedResponseModel)
async def get_knowledge_file_status(
    knowledge_file_id: str = Body(..., embed=True),
    login_user: UserPayload = Depends(get_login_user)
):
    try:
        # 验证用户权限
        await KnowledgeFileService.verify_user_permission(knowledge_file_id, login_user.user_id)
        knowledge_file = await KnowledgeFileService.select_knowledge_file_by_id(knowledge_file_id)
        result = knowledge_file.to_dict()

        # 动态添加预签名 URL
        if result.get('oss_url'):
            from agentchat.settings import app_settings
            base_url = app_settings.storage.minio.base_url.rstrip('/')
            oss_url = result['oss_url']

            # 如果存储的是基础 URL（不含查询参数），则转换为预签名 URL
            if oss_url.startswith(base_url) and '?' not in oss_url:
                # 从基础 URL 中提取 object key（移除 /agentchat/ 前缀）
                object_key = oss_url[len(base_url):].lstrip('/')
                if object_key.startswith("agentchat/"):
                    object_key = object_key[len("agentchat/"):]
                # 生成预签名 URL
                result['oss_url'] = storage_client.sign_url_for_get(object_key)

        return resp_200(data=result)
    except Exception as err:
        return resp_500(message=str(err))
