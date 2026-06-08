import asyncio
from sqlalchemy import text
from app.database import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as s:
        r = await s.execute(text("SELECT name, balance_usd, status, hard_stop_threshold, warning_threshold, is_active FROM providers"))
        for row in r.all():
            print(dict(row._mapping))

asyncio.run(main())
