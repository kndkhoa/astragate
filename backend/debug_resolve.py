import asyncio
from sqlalchemy import select, text
from app.database import AsyncSessionLocal
from app.models.model import Model
from app.services.provider_balance import resolve_provider_for_request, check_provider_status

async def main():
    async with AsyncSessionLocal() as s:
        # Find the gemini model's provider_id
        r = await s.execute(text("SELECT id, model_id, provider_id, is_active FROM models WHERE model_id LIKE '%gemini%'"))
        for row in r.all():
            print("MODEL:", dict(row._mapping))

        # Get gemini provider row raw
        r2 = await s.execute(text("SELECT id, name, balance_usd, status, hard_stop_threshold, is_active, fallback_provider_id FROM providers WHERE name='gemini'"))
        prov = r2.first()
        print("PROVIDER raw:", dict(prov._mapping))
        pid = prov._mapping["id"]

        # Resolve
        status = await check_provider_status(__import__("uuid").UUID(pid) if isinstance(pid, str) else pid, s)
        print("STATUS obj:", status)

        decision = await resolve_provider_for_request(__import__("uuid").UUID(pid) if isinstance(pid, str) else pid, s)
        print("DECISION:", decision.reason, "should_block=", decision.should_block)

asyncio.run(main())
