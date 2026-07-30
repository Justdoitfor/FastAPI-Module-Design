from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from auth.database.session import get_db
from auth.repositories.user_repository import UserRepository


def get_user_repository(db: AsyncSession = Depends(get_db)):
    return UserRepository(db)
