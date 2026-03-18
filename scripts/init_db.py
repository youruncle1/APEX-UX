import asyncio
import sys
import os
import argparse
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

load_dotenv()
os.environ["DATABASE_URL"] = os.environ["DATABASE_URL"].replace("@postgres:", "@localhost:")

from src.database.connection import init_db, drop_db


async def main(auto_yes: bool):
    if not auto_yes:
        print("### WARNING: This will DELETE ALL DATA in the database. ###")
        confirm = input("\tAre you sure you want to continue? (y/n): ").strip().lower()

        if confirm not in ("y", "yes"):
            print("Aborted. Database was not modified.")
            return

    print("Dropping all database tables...")
    await drop_db()

    print("Initializing database tables...")
    await init_db()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reset and initialize the database")
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Automatically confirm database reset",
    )
    args = parser.parse_args()

    asyncio.run(main(auto_yes=args.yes))
