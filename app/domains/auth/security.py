import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum

import jwt
from pwdlib import PasswordHash

from app.core.config import settings
from app.domains.auth.exceptions import InvalidTokenError

password_hasher = PasswordHash.recommended()


def hash_password(plain_password: str) -> str:
    return password_hasher.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return password_hasher.verify(plain_password, hashed_password)


def need_rehash(hashed_password: str) -> bool:
    # 第二个返回值是新hash；这里只看：是否算法过时需要重哈希
    _, updated_hash = password_hasher.verify_and_update("", hashed_password)
    return updated_hash is not None

class TokenType(Enum):
    ACCESS = 'access'
    REFRESH = 'refresh'

def create_access_token(*, user_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "type": TokenType.ACCESS.value,
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(
        payload,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )

def decode_access_token(token: str) -> int:
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM]
        )
    except jwt.PyJWTError as exc:
        raise InvalidTokenError() from exc

    if payload.get('type') != TokenType.ACCESS.value:
        raise InvalidTokenError()

    try:
        return int(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise InvalidTokenError() from exc