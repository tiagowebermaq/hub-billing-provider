from redis.asyncio import Redis


class IdempotencyGuard:
    """Marca chaves já processadas no Redis (SET NX + TTL). Seguro com vários workers."""

    def __init__(self, redis: Redis, ttl_seconds: int, prefix: str = "idem") -> None:
        self._redis = redis
        self._ttl = ttl_seconds
        self._prefix = prefix

    async def first_time(self, key: str) -> bool:
        return bool(await self._redis.set(f"{self._prefix}:{key}", "1", nx=True, ex=self._ttl))

    async def release(self, key: str) -> None:
        """Libera a chave quando o processamento falhou e precisa ser repetido."""
        await self._redis.delete(f"{self._prefix}:{key}")
