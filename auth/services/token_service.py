from typing import TypedDict

from auth.core.redis import redis_client
from enum import Enum
import json


class RefreshTokenStatus(str, Enum):
    VALID = "valid"
    REUSED = "reused"
    INVALID = "invalid"


class RefreshTokenData(TypedDict):
    user_id: int
    family_id: str


class TokenService:
    REFRESH_TOKEN_PREFIX = "auth:refresh"
    REVOKED_REFRESH_TOKEN_PREFIX = "auth:refresh:revoked"
    FAMILY_PREFIX = "auth:family"

    @classmethod
    def _refresh_key(cls, jti: str) -> str:
        return f"{cls.REFRESH_TOKEN_PREFIX}:{jti}"

    @classmethod
    def _revoked_refresh_key(cls, jti: str) -> str:
        return f"{cls.REVOKED_REFRESH_TOKEN_PREFIX}:{jti}"

    @classmethod
    def _family_key(cls, family_id: str) -> str:
        return f"{cls.FAMILY_PREFIX}:{family_id}"

    async def register_refresh_token(
            self,
            jti: str,
            user_id: int,
            family_id: str,
            ttl: int,
    ) -> None:
        data = {
            "user_id": user_id,
            "family_id": family_id,
        }
        key = self._refresh_key(jti)
        await redis_client.set(key, json.dumps(data), ex=ttl)

    async def revoke_refresh_token(
            self,
            jti: str,
    ) -> None:
        key = self._refresh_key(jti)
        await redis_client.delete(key)

    async def is_family_revoked(
            self,
            family_id: str,
    ) -> bool:
        key = self._family_key(family_id)
        result = await redis_client.get(key)
        return result is not None

    async def consume_refresh_token(
            self,
            jti: str,
    ) -> tuple[RefreshTokenStatus, RefreshTokenData | None]:
        key = self._refresh_key(jti)
        revoked_key = self._revoked_refresh_key(jti)
        script = """
        local value = redis.call('GET', KEYS[1])
        
        if value then
            redis.call(
                'DEL',
                KEYS[1]
            )
            redis.call(
                'SET',
                KEYS[2],
                value,
                'EX',
                ARGV[1]
            )
            return {1, value}
        end

        local revoked = redis.call(
            'GET',
            KEYS[2]
        )

        if revoked then
            return {2, revoked}
        end

        return {0, nil}
        """
        result = await redis_client.eval(
            script,
            2,
            key,
            revoked_key,
            86400,
        )
        status = result[0]

        if status == 1:
            data = json.loads(result[1])
            return (
                RefreshTokenStatus.VALID,
                RefreshTokenData(user_id=data["user_id"], family_id=data["family_id"])
            )

        if status == 2:
            data = json.loads(result[1])

            return (
                RefreshTokenStatus.REUSED,
                RefreshTokenData(user_id=data["user_id"], family_id=data["family_id"])
            )

        return (
            RefreshTokenStatus.INVALID,
            None
        )

    async def revoke_family(
            self,
            family_id: str,
            ttl: int,
    ) -> None:
        key = self._family_key(family_id)
        await redis_client.set(
            key,
            '1',
            ex=ttl,
        )
