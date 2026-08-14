from datetime import datetime, timezone

from auth.core.security import hash_password, verify_password
from auth.core.jwt import create_access_token, create_refresh_token, verify_refresh_token, decode_token
from auth.repositories.user_repository import UserRepository
from auth.schemas.auth import UserRegisterRequest, TokenResponse
from auth.models.user import User
from auth.core.exceptions import (UsernameAlreadyExists,
                                  EmailAlreadyExists,
                                  InvalidPassword,
                                  UserNotExist, InvalidRefreshToken)
from auth.core.logger import logger
from auth.services.token_service import TokenService


class AuthService:
    def __init__(
            self,
            user_repository: UserRepository,
            token_service: TokenService
    ):
        self.user_repository = user_repository
        self.token_service = token_service

    async def register(
            self,
            data: UserRegisterRequest
    ) -> User:
        # 检查用户名是否存在
        exist_user = await self.user_repository.get_by_username(data.username)
        if exist_user:
            logger.warning(f'User {data.username} already exists')
            raise UsernameAlreadyExists()
        # 检查邮箱是否存在
        exist_user = await self.user_repository.get_by_email(data.email)
        if exist_user:
            logger.warning(f'Email {data.email} already exists')
            raise EmailAlreadyExists()
        # 密码加密
        password_hash = hash_password(data.password)
        # 创建ORM对象
        user = User(
            username=data.username,
            email=data.email,
            password_hash=password_hash,
        )
        return await self.user_repository.create(user)

    async def login(
            self,
            username: str,
            password: str
    ):
        user = await self.user_repository.get_by_username(username)
        if not user:
            raise UserNotExist()
        is_valid = verify_password(password, user.password_hash)
        if not is_valid:
            raise InvalidPassword()
        access_token = create_access_token(user.id)
        refresh_token = create_refresh_token(user.id)

        refresh_payload = decode_token(refresh_token)
        jti = refresh_payload.get("jti")
        exp = refresh_payload.get("exp")
        now = int(datetime.now(timezone.utc).timestamp())
        ttl = exp - now
        if ttl <= 0:
            raise InvalidRefreshToken()

        await self.token_service.register_refresh_token(
            jti=jti,
            user_id=user.id,
            ttl=ttl,
        )
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
        )

    async def refresh_access_token(
            self,
            refresh_token: str
    ):
        try:
            payload = verify_refresh_token(refresh_token)
        except ValueError:
            raise InvalidRefreshToken()
        jti = payload.get("jti")
        if not jti:
            raise InvalidRefreshToken()

        user_id = await self.token_service.get_refresh_token_user_id(jti)
        if not user_id:
            raise InvalidRefreshToken()

        user = await self.user_repository.get_by_id(int(user_id))
        if not user:
            raise UserNotExist()
        access_token = create_access_token(user.id)
        return {"access_token": access_token, "token_type": "bearer"}

    async def logout(
            self,
            refresh_token: str,
    ) -> None:
        try:
            payload = verify_refresh_token(refresh_token)
        except ValueError:
            raise InvalidRefreshToken()

        jti = payload.get("jti")
        if not jti:
            raise InvalidRefreshToken()

        await self.token_service.revoke_refresh_token(jti=jti)
