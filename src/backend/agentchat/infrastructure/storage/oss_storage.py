from pathlib import Path
from urllib.parse import quote


class OssStorageAdapter:
    """OSS/MinIO 统一源文件适配器，并保留本地镜像便于原型演示。"""

    def __init__(self):
        self.local_root = Path(".rag_storage")
        self.local_root.mkdir(parents=True, exist_ok=True)

    async def upload(self, object_key: str, data: bytes) -> str:
        self._local_path(object_key).write_bytes(data)
        try:
            from agentchat.services.storage import storage_client

            storage_client.upload_file(object_key, data)
        except Exception:
            pass
        return object_key

    async def download(self, object_key: str) -> bytes:
        try:
            from agentchat.services.storage import storage_client

            target = self._local_path(object_key)
            storage_client.download_file(object_key, str(target))
            if target.exists():
                return target.read_bytes()
        except Exception:
            pass

        target = self._local_path(object_key)
        if target.exists():
            return target.read_bytes()
        raise FileNotFoundError(f"源文件不存在: {object_key}")

    async def presigned_url(self, object_key: str, expires: int = 3600) -> str:
        try:
            from agentchat.services.storage import storage_client

            url = storage_client.sign_url_for_get(object_key, expires)
            if url:
                return url
        except Exception:
            pass
        return f"local://{quote(object_key)}"

    def _local_path(self, object_key: str) -> Path:
        safe_key = object_key.replace("\\", "/").replace("..", "_")
        path = self.local_root / safe_key
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

