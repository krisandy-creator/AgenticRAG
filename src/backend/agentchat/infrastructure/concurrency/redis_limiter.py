import asyncio

import redis.asyncio as aioredis

from agentchat.settings import app_settings


class RedisConcurrencyLimiter:
    """基于 Redis 的分布式并发槽位，用于 Vision / Embedding 全局限流。"""

    def __init__(self, redis_url: str | None = None):
        self.redis_url = redis_url or app_settings.redis.get("endpoint") or "redis://localhost:6379/0"

    async def _client(self):
        return aioredis.from_url(self.redis_url, decode_responses=True)

    async def acquire(self, key: str, limit: int, *, ttl_seconds: int = 300, poll_interval: float = 0.15) -> None:
        if limit <= 0:
            return

        redis = await self._client()
        try:
            while True:
                current = await redis.incr(key)
                if current == 1:
                    await redis.expire(key, ttl_seconds)
                if current <= limit:
                    return
                await redis.decr(key)
                await asyncio.sleep(poll_interval)
        finally:
            await redis.aclose()

    async def release(self, key: str) -> None:
        redis = await self._client()
        try:
            value = await redis.decr(key)
            if value <= 0:
                await redis.delete(key)
        finally:
            await redis.aclose()


class LocalConcurrencyLimiter:
    """单进程内并发限制，作为 Redis 不可用时的降级。"""

    _semaphores: dict[str, asyncio.Semaphore] = {}

    @classmethod
    def semaphore(cls, key: str, limit: int) -> asyncio.Semaphore:
        cache_key = f"{key}:{limit}"
        if cache_key not in cls._semaphores:
            cls._semaphores[cache_key] = asyncio.Semaphore(max(limit, 1))
        return cls._semaphores[cache_key]
