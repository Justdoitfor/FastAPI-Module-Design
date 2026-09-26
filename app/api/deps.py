from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session_factory


async def get_db():
    async with async_session_factory() as session:
        yield session


DbDep = Annotated[AsyncSession, Depends(get_db)]
