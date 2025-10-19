from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import sessionmaker
from typing import Dict, Any
import ssl

from config import DATABASE_URL

# Build an SSL context for asyncpg
ssl_ctx = ssl.create_default_context()

engine: AsyncEngine = create_async_engine(
    DATABASE_URL,                          
    echo=False,
    pool_pre_ping=True,
    connect_args={"ssl": ssl_ctx},         
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession
)

async def init_db(metadata) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
