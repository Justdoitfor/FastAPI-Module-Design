from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from auth.models.user import User
from auth.repositories.base import BaseRepository


class UserRepository(BaseRepository):
    async def get_by_username(
            self,
            username: str,
    ) -> User | None:
        stmt = (
            select(User)
            .where(User.username == username)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_email(
            self,
            email: str,
    ) -> User | None:
        stmt = (
            select(User)
            .where(User.email == email)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def create(
            self,
            user: User,
    ) -> User:
        self.db.add(user)
        await self.db.commit()
        await self.db.refresh(user)
        return user
