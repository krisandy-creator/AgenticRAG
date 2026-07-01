import asyncio
import json

import aiohttp
from agentchat.settings import app_settings, initialize_app_settings
from agentchat.schema.rerank import RerankResultModel


class Reranker:

    @classmethod
    async def request_rerank(cls, query, documents, top_n: int | None = None):
        if not documents:
            return []

        rerank_cfg = app_settings.multi_models.rerank
        api_key = getattr(rerank_cfg, "api_key", "") or ""
        if not api_key.strip():
            raise ValueError("rerank api_key 未配置")

        requested_top_n = top_n or len(documents)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": rerank_cfg.model_name,
            "input": {
                "query": query,
                "documents": documents,
            },
            "parameters": {
                "return_documents": True,
                "top_n": min(requested_top_n, len(documents)),
            },
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url=rerank_cfg.base_url,
                headers=headers,
                data=json.dumps(payload),
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return result["output"]["results"]
                body = await response.text()
                raise RuntimeError(f"rerank HTTP {response.status}: {body[:500]}")

    @classmethod
    async def rerank_documents(cls, query, documents, top_n: int | None = None):
        final_documents = []
        original_documents = documents

        results = await cls.request_rerank(query, documents, top_n=top_n)

        for result in results:
            result["document"] = original_documents[result["index"]]

            final_documents.append(
                RerankResultModel(
                    query=query,
                    content=result["document"],
                    score=result["relevance_score"],
                    index=result["index"],
                )
            )
        return final_documents


if __name__ == "__main__":
    asyncio.run(initialize_app_settings("../../config.yaml"))

    asyncio.run(
        Reranker.rerank_documents(
            query="什么是文本排序模型",
            documents=[
                "文本排序模型广泛用于搜索引擎和推荐系统中，它们根据文本相关性对候选文本进行排序",
                "量子计算是计算科学的一个前沿领域",
                "预训练语言模型的发展给文本排序模型带来了新的进展",
            ],
        )
    )
