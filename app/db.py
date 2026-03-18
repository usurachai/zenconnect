import structlog
import asyncpg
from typing import Optional

from app.config import get_settings

logger = structlog.get_logger()

class Database:
    pool: Optional[asyncpg.Pool] = None

db = Database()

async def init_pool() -> None:
    settings = get_settings()
    logger.info("Initializing asyncpg pool", url=settings.database_url)
    db.pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=1,
        max_size=settings.db_pool_max_size,
    )

async def close_pool() -> None:
    if db.pool:
        logger.info("Closing asyncpg pool")
        await db.pool.close()

def get_pool() -> asyncpg.Pool:
    if db.pool is None:
        raise RuntimeError("Database pool is not initialized")
    return db.pool
