import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from src.database.connection import engine, AsyncSessionLocal
from src.database.models import Base

SEED_FILE = os.path.join(os.path.dirname(__file__), "seed_personas.sql")


async def ensure_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("[ensure_db] Database tables verified / created.")

    seed_sql = open(SEED_FILE).read()
    async with AsyncSessionLocal() as session:
        await session.execute(text(seed_sql))
        await session.commit()

    print("[ensure_db] Personas seeded.")


if __name__ == "__main__":
    asyncio.run(ensure_db())
