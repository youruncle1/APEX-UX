import asyncio
import os
import sys

# Must run before any src.* imports so Python can resolve the root
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import text  # noqa: E402
from src.database.connection import engine, AsyncSessionLocal  # noqa: E402
from src.database.models import Base  # noqa: E402

load_dotenv()

SEED_FILE = os.path.join(os.path.dirname(__file__), "seed_personas.sql")


async def ensure_db():
    # Create tables (only if they don't exist)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("[ensure_db] Database tables verified / created.")

    # Seed personas from .sql file
    seed_sql = open(SEED_FILE).read()
    async with AsyncSessionLocal() as session:
        await session.execute(text(seed_sql))
        await session.commit()
    print("[ensure_db] Personas seeded.")


if __name__ == "__main__":
    asyncio.run(ensure_db())
