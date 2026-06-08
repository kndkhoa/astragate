import asyncio
from sqlalchemy import text
from app.database import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as s:
        await s.execute(text("UPDATE providers SET balance_usd = 100.0, status = 'normal', hard_stop_activated_at = NULL"))
        await s.commit()
        r = await s.execute(text("SELECT name, balance_usd, status FROM providers"))
        for row in r.all():
            print(dict(row._mapping))

asyncio.run(main())
