import hashlib
import math
import re


class EmbeddingProvider:
    def __init__(self):
        self.last_provider = "unknown"

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        try:
            from agentchat.services.rag.embedding import get_embedding

            embeddings = await get_embedding(texts)
            if embeddings:
                self.last_provider = "configured"
                return embeddings
        except Exception:
            pass
        self.last_provider = "feature_hash"
        return [self._hash_embedding(text) for text in texts]

    @staticmethod
    def _hash_embedding(text: str, dim: int = 256) -> list[float]:
        values = [0.0] * dim
        tokens = re.findall(r"[A-Za-z0-9_]{2,}", text.lower())
        for segment in re.findall(r"[\u4e00-\u9fff]+", text):
            for size in (2, 3, 4):
                tokens.extend(segment[index:index + size] for index in range(0, max(len(segment) - size + 1, 0)))

        if not tokens:
            tokens = [text.strip()] if text.strip() else ["empty"]

        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % dim
            sign = 1.0 if int.from_bytes(digest[4:], "big") % 2 == 0 else -1.0
            values[bucket] += sign

        norm = math.sqrt(sum(value * value for value in values)) or 1.0
        return [value / norm for value in values]
