from sqlalchemy.ext.asyncio import AsyncSession


class BaseRepository:
    """Repository 基类"""

    def __init__(self, db: AsyncSession):
        self.db = db
