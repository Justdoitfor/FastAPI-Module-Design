from auth.core.redis import redis_client


class TokenService:
    REFRESH_TOKEN_PREFIX = "auth:refresh"

    @classmethod
    def _refresh_key(cls, jti: str) -> str:
        return f"{cls.REFRESH_TOKEN_PREFIX}:{jti}"

    async def register_refresh_token(
            self,
            jti: str,
            user_id: int,
            ttl: int,
    ) -> None:
        key = self._refresh_key(jti)
        await redis_client.set(key, str(user_id), ex=ttl)

    async def get_refresh_token_user_id(
            self,
            jti: str,
    ) -> int | None:
        key = self._refresh_key(jti)
        user_id = await redis_client.get(key)
        if user_id is None:
            return None
        return int(user_id)
