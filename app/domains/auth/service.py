import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from pydantic import EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ConflictError
from app.domains.auth.exceptions import (
    InactiveUserError,
    InvalidCredentialsError,
    InvalidTokenError,
    TokenReuseDetectedError,
)

from app.domains.auth.models import RefreshToken, User
from app.domains.auth.repository import RefreshTokenRepository, UserRepository
from app.domains.auth.schemas import TokenPair, UserCreate
from app.domains.auth.security import create_access_token, hash_password, need_rehash, verify_password


def _new_raw_token() -> str:
    return secrets.token_urlsafe(32)


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


class AuthService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.users = UserRepository(session)
        self.refresh_tokens = RefreshTokenRepository(session)

    async def register(self, data: UserCreate) -> User:
        if await self.users.get_by_email(data.email) is not None:
            raise ConflictError("改邮箱已被注册")
        user = User(
            email=data.email,
            hashed_password=hash_password(data.password),
        )
        await self.users.create(user)
        await self.session.commit()
        return user

    async def login(self, email: EmailStr, password: str) -> TokenPair:
        user = await self.users.get_by_email(email)
        dummy_hash = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHRzYWx0$dGVzdGRpZ2VzdGRpZ2VzdA"
        if user is None:
            verify_password(password, dummy_hash)
            raise InvalidCredentialsError()
        if not verify_password(password, user.hashed_password):
            raise InvalidCredentialsError()
        if need_rehash(user.hashed_password):
            await self.users.update(user, {"hashed_password": hash_password(password)})
        return await self._issue_tokens(user.id, family_id=None)

    async def refresh(self, raw_token: str) -> TokenPair:
        token_hash = _hash_token(raw_token)
        stored = await self.refresh_tokens.get_by_hash(token_hash)

        if stored is None:
            raise InvalidTokenError()

        now = datetime.now(timezone.utc)
        if stored.revoked_at is not None:
            raise InvalidTokenError()

        if stored.expires_at.timestamp() < now.timestamp():
            raise InvalidTokenError()

        if stored.used_at is not None:
            await self.refresh_tokens.revoke_family(stored.family_id)
            await self.session.commit()
            raise TokenReuseDetectedError()

        stored.used_at = now
        await self.session.flush()
        return await self._issue_tokens(stored.user_id, family_id=stored.family_id)

    async def logout(self, raw_token: str) -> None:
        stored = await self.refresh_tokens.get_by_hash(_hash_token(raw_token))
        if stored is not None and stored.revoked_at is None:
            await self.refresh_tokens.revoke(stored)
        await self.session.commit()

    async def logout_all_devices(self, user_id: int) -> None:
        family_ids = await self.refresh_tokens.list_active_family_ids(user_id)
        for family_id in family_ids:
            await self.refresh_tokens.revoke_family(family_id)
        await self.session.commit()

    async def _issue_tokens(self, user_id: int, *, family_id: str | None) -> TokenPair:
        raw_token = _new_raw_token()
        record = RefreshToken(
            user_id=user_id,
            token_hash=_hash_token(raw_token),
            family_id=family_id or uuid.uuid4().hex,
            expires_at=datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        )
        await self.refresh_tokens.create(record)
        await self.session.commit()
        return TokenPair(
            access_token=create_access_token(user_id=user_id),
            refresh_token=raw_token,
        )
