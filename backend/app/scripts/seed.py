"""
Database seed script.

Inserts default providers (Groq, DeepSeek, Gemini), default models with
pricing, and the global markup_config row (20%).

Idempotent: uses INSERT ... ON CONFLICT DO NOTHING so it is safe to run
multiple times.

Usage:
    cd backend
    python -m app.scripts.seed
"""
import asyncio
import sys
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

PROVIDERS = [
    {
        "name": "groq",
        "display_name": "Groq",
        "balance_usd": Decimal("0"),
        "warning_threshold": Decimal("10.00"),
        "hard_stop_threshold": Decimal("2.00"),
        "status": "normal",
        "is_active": True,
    },
    {
        "name": "deepseek",
        "display_name": "DeepSeek",
        "balance_usd": Decimal("0"),
        "warning_threshold": Decimal("10.00"),
        "hard_stop_threshold": Decimal("2.00"),
        "status": "normal",
        "is_active": True,
    },
    {
        "name": "gemini",
        "display_name": "Google Gemini",
        "balance_usd": Decimal("0"),
        "warning_threshold": Decimal("10.00"),
        "hard_stop_threshold": Decimal("2.00"),
        "status": "normal",
        "is_active": True,
    },
]

# (provider_name, model_id, display_name, input_price_per_1m, output_price_per_1m)
MODELS = [
    (
        "groq",
        "groq/llama-3.1-8b-instant",
        "Llama 3.1 8B Instant",
        Decimal("0.050000"),
        Decimal("0.080000"),
    ),
    (
        "deepseek",
        "deepseek/deepseek-chat",
        "DeepSeek Chat",
        Decimal("0.140000"),
        Decimal("0.280000"),
    ),
    (
        "gemini",
        "gemini/gemini-1.5-flash",
        "Gemini 1.5 Flash",
        Decimal("0.075000"),
        Decimal("0.300000"),
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_provider_id(session: AsyncSession, name: str) -> str:
    result = await session.execute(
        text("SELECT id FROM providers WHERE name = :name"),
        {"name": name},
    )
    row = result.fetchone()
    if row is None:
        raise RuntimeError(f"Provider '{name}' not found after insert")
    return str(row[0])


# ---------------------------------------------------------------------------
# Main seed function
# ---------------------------------------------------------------------------

async def seed() -> None:
    """Seed the database with default data."""
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    async with session_factory() as session:
        async with session.begin():
            # ── 1. Insert providers (without fallback chains first) ──────────
            print("Seeding providers...")
            for p in PROVIDERS:
                await session.execute(
                    text(
                        """
                        INSERT INTO providers
                            (name, display_name, balance_usd, warning_threshold,
                             hard_stop_threshold, status, is_active)
                        VALUES
                            (:name, :display_name, :balance_usd, :warning_threshold,
                             :hard_stop_threshold, :status, :is_active)
                        ON CONFLICT (name) DO NOTHING
                        """
                    ),
                    p,
                )

            # ── 2. Set fallback chains: groq → deepseek → gemini ─────────────
            print("Setting fallback chains...")
            groq_id = await _get_provider_id(session, "groq")
            deepseek_id = await _get_provider_id(session, "deepseek")
            gemini_id = await _get_provider_id(session, "gemini")

            # groq falls back to deepseek
            await session.execute(
                text(
                    "UPDATE providers SET fallback_provider_id = :fallback_id "
                    "WHERE name = 'groq' AND fallback_provider_id IS NULL"
                ),
                {"fallback_id": deepseek_id},
            )
            # deepseek falls back to gemini
            await session.execute(
                text(
                    "UPDATE providers SET fallback_provider_id = :fallback_id "
                    "WHERE name = 'deepseek' AND fallback_provider_id IS NULL"
                ),
                {"fallback_id": gemini_id},
            )

            # ── 3. Insert models ─────────────────────────────────────────────
            print("Seeding models...")
            provider_ids = {
                "groq": groq_id,
                "deepseek": deepseek_id,
                "gemini": gemini_id,
            }
            for provider_name, model_id, display_name, input_price, output_price in MODELS:
                await session.execute(
                    text(
                        """
                        INSERT INTO models
                            (provider_id, model_id, display_name,
                             input_price_per_1m, output_price_per_1m, is_active)
                        VALUES
                            (:provider_id, :model_id, :display_name,
                             :input_price_per_1m, :output_price_per_1m, true)
                        ON CONFLICT (provider_id, model_id) DO NOTHING
                        """
                    ),
                    {
                        "provider_id": provider_ids[provider_name],
                        "model_id": model_id,
                        "display_name": display_name,
                        "input_price_per_1m": input_price,
                        "output_price_per_1m": output_price,
                    },
                )

            # ── 4. Insert global markup_config row (20%) ─────────────────────
            # Note: ON CONFLICT won't fire for NULLs in PostgreSQL unique constraints,
            # so we use WHERE NOT EXISTS for idempotency.
            print("Seeding global markup config (20%)...")
            await session.execute(
                text(
                    """
                    INSERT INTO markup_config (scope, provider_id, model_id, markup_rate)
                    SELECT 'global', NULL, NULL, 0.20
                    WHERE NOT EXISTS (
                        SELECT 1 FROM markup_config
                        WHERE scope = 'global'
                          AND provider_id IS NULL
                          AND model_id IS NULL
                    )
                    """
                )
            )

    await engine.dispose()
    print("Seed complete.")


if __name__ == "__main__":
    asyncio.run(seed())
