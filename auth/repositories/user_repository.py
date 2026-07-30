from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from auth.models.user import User
from auth.repositories.base import BaseRepository


class UserRepository(BaseRepository):
    async def get_by_username(
            self,
            username: str,
    ) -> User | None:
        ...

    async def get_by_email(
            self,
            email: str,
    ) -> User | None:
        ...

    async def create(
            self,
            user: User,
    ) -> User:
        ...


