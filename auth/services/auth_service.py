from auth.core.security import hash_password
from auth.repositories.user_repository import UserRepository
from auth.schemas.auth import UserRegisterRequest
from auth.models.user import User
from auth.core.exceptions import UsernameAlreadyExists, EmailAlreadyExists
from auth.core.logger import logger


class AuthService:
    def __init__(self, user_repository: UserRepository):
        self.user_repository = user_repository

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
