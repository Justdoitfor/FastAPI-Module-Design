from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession
)

from app.core.config import settings

# 使用异步engine，提高吞吐
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,  # 是否打印SQL调试
)
# 会话工厂
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)
