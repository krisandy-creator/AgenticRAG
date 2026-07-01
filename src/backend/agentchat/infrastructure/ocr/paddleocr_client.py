import os

import httpx


class PaddleOcrClient:
    """调用本地 PaddleOCR 服务解析扫描 PDF。"""

    def __init__(self):
        self.base_url = os.getenv("PADDLEOCR_URL", "http://127.0.0.1:8791").rstrip("/")

    async def parse_pdf(self, file_url: str) -> list[dict]:
        if not self.base_url:
            raise RuntimeError("PaddleOCR 服务未配置，请设置 PADDLEOCR_URL 后重新解析扫描 PDF。")

        async with httpx.AsyncClient(timeout=600) as client:
            response = await client.post(f"{self.base_url}/parse_pdf", json={"file_url": file_url})
            response.raise_for_status()
            data = response.json()
            return data.get("pages", data if isinstance(data, list) else [])
