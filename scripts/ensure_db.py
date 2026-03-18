"""
Run on every container startup. Creates database tables if they don't exist.
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

from src.database.connection import engine
from src.database.models import Base


async def ensure_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("[ensure_db] Database tables verified / created.")


if __name__ == "__main__":
    asyncio.run(ensure_db())