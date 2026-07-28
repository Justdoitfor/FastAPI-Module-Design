from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession
)

from auth.core.config import settings
# 使用异步engine，提高吞吐
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=True, # 打印SQL调试
)
# 会话工厂
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
