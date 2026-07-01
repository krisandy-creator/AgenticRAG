import asyncio
from typing import Union, List

from openai import AsyncOpenAI
from agentchat.settings import app_settings, initialize_app_settings

embedding_model = app_settings.multi_models.embedding.model_name
embedding_client = AsyncOpenAI(base_url=app_settings.multi_models.embedding.base_url,
                               api_key=app_settings.multi_models.embedding.api_key)

async def get_embedding(query: Union[str, List[str]]):
    return await get_embedding_batched(query)


async def get_embedding_batched(
    query: Union[str, List[str]],
    *,
    api_batch_size: int = 10,
    max_concurrency: int = 5,
):
    if isinstance(query, str):
        responses = await embedding_client.embeddings.create(
            model=embedding_model,
            input=query,
            encoding_format="float",
        )
        return responses.data[0].embedding

    if not query:
        return []

    if len(query) <= api_batch_size:
        responses = await embedding_client.embeddings.create(
            model=embedding_model,
            input=query,
            encoding_format="float",
        )
        return [response.embedding for response in responses.data]

    semaphore = asyncio.Semaphore(max_concurrency)

    async def process_batch(batch):
        async with semaphore:
            responses = await embedding_client.embeddings.create(
                model=embedding_model,
                input=batch,
                encoding_format="float",
            )
            return [response.embedding for response in responses.data]

    batches = [query[i: i + api_batch_size] for i in range(0, len(query), api_batch_size)]
    results = await asyncio.gather(*[process_batch(batch) for batch in batches])
    return [embedding for batch_result in results for embedding in batch_result]


if __name__ == "__main__":
    asyncio.run(initialize_app_settings("../../config.yaml"))

    asyncio.run(get_embedding(["大模型"]))
