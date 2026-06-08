"""
Setup SQLite local dev database.
Creates all schemas and seeds default data.
"""
import asyncio
from app.database import engine
from app.models.base import Base
from app.scripts.seed import seed

async def setup_db():
    print("Creating SQLite database tables...")
    async with engine.begin() as conn:
        # Import all models to register them with metadata
        import app.models  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)
    print("Database tables created successfully.")
    
    print("Seeding database...")
    await seed()
    print("Setup database completed successfully.")

if __name__ == "__main__":
    asyncio.run(setup_db())
