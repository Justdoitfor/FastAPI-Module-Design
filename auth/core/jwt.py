import uuid
from datetime import timedelta, datetime, timezone
from jose import jwt, JWTError
from auth.core.config import settings


def create_family_id() -> str:
    return str(uuid.uuid4())


def create_token(
        user_id: int,
        token_type: str,
        expires_delta: timedelta,
        family_id: str | None = None
) -> str:
    """创建JWT Token"""
    now = datetime.now(timezone.utc)
    expire = now + expires_delta
    payload = {
        "sub": str(user_id),  # subject-> 当前token属于哪个用户
        "type": token_type,
        "jti": str(uuid.uuid4()),  # jwt id
        "iat": now,  # Issued at -> Token签发时间
        "exp": expire  # token 过期时间
    }
    if family_id:
        payload["family_id"] = family_id
    return jwt.encode(
        payload,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM
    )


def create_access_token(user_id: int):
    """ 创建 Access Token """
    return create_token(
        user_id=user_id,
        token_type="access",
        expires_delta=timedelta(minutes=int(settings.ACCESS_TOKEN_EXPIRE_MINUTES)),
    )


def create_refresh_token(user_id: int, family_id: str):
    """ 创建 Refresh Token """
    return create_token(
        user_id=user_id,
        token_type="refresh",
        family_id=family_id,
        expires_delta=timedelta(days=int(settings.REFRESH_TOKEN_EXPIRE_DAYS)),
    )


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM]
        )
    except JWTError:
        raise ValueError("Invalid token")


def verify_refresh_token(token: str) -> dict:
    payload = decode_token(token)
    if payload.get("type") != "refresh":
        raise ValueError("Invalid refresh token")
    if not payload.get("sub"):
        raise ValueError("Invalid refresh token")
    return payload
