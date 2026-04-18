from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from src.config import get_settings, require_database_url
from src.log import get_logger
from .models import Base

logger = get_logger(__name__)

_settings = get_settings()
DATABASE_URL = require_database_url(_settings)

engine = create_async_engine(
    DATABASE_URL,
    pool_size=_settings.db_pool_size,
    max_overflow=_settings.db_max_overflow,
    echo=False,
)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)



async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created")


async def drop_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    logger.info("Database tables dropped")
