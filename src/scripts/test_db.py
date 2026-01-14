import asyncio
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.database.connection import AsyncSessionLocal
from src.database.models import Evaluation, EvaluationStatus
from sqlalchemy import select

async def main():
    print("Testing database connection...")
    
    async with AsyncSessionLocal() as session:
        # Create a test evaluation
        eval = Evaluation(
            name="Test Evaluation",
            base_url="https://example.com",
            status=EvaluationStatus.PENDING
        )
        session.add(eval)
        await session.commit()
        print(f"✓ Created evaluation: {eval.id}")
        
        # Query it back
        result = await session.execute(select(Evaluation))
        evals = result.scalars().all()
        print(f"✓ Found {len(evals)} evaluation(s) in database")
        
        for e in evals:
            print(f"  - {e.name} ({e.status.value}) at {e.base_url}")
    
    print("\n✓ Database connection works!")

if __name__ == "__main__":
    asyncio.run(main())