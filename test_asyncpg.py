# test_asyncpg.py
import asyncio, os, asyncpg
from dotenv import load_dotenv
from sqlalchemy.engine import make_url
import ssl

load_dotenv()
u = make_url(os.getenv("DATABASE_URL", ""))  # can handle +asyncpg

ssl_ctx = ssl.create_default_context()

async def main():
    conn = await asyncpg.connect(
        user=u.username,
        password=u.password,
        host=u.host,
        port=u.port or 5432,
        database=u.database,
        ssl=ssl_ctx
    )
    print("Connected OK")
    await conn.close()

asyncio.run(main())
