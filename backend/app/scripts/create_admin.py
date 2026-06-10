import asyncio
import uuid
import bcrypt
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.config import settings
from app.models.user import User
from app.models.credit import CreditAccount, CreditTransaction

async def create_admin():
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    admin_email = "admin@astragate.io"
    admin_password = "admin123"

    async with session_factory() as session:
        async with session.begin():
            # Check if admin already exists
            result = await session.execute(select(User).where(User.email == admin_email))
            existing_user = result.scalar_one_or_none()
            if existing_user:
                print(f"Admin user '{admin_email}' already exists.")
                return

            # Hash password
            salt = bcrypt.gensalt(rounds=12)
            password_hash = bcrypt.hashpw(admin_password.encode("utf-8"), salt).decode("utf-8")

            # Create admin user
            admin_id = uuid.uuid4()
            user = User(
                id=admin_id,
                email=admin_email,
                password_hash=password_hash,
                role="admin",
                is_active=True,
                failed_login_attempts=0,
            )
            session.add(user)

            # Create credit account
            credit_account = CreditAccount(
                id=uuid.uuid4(),
                user_id=admin_id,
                balance_usd=Decimal("100.0"),
            )
            session.add(credit_account)

            # Record transaction
            transaction = CreditTransaction(
                id=uuid.uuid4(),
                user_id=admin_id,
                type="free_credit",
                amount_usd=Decimal("100.0"),
                balance_after=Decimal("100.0"),
                description="Admin signup credit"
            )
            session.add(transaction)
            
            print(f"Admin user '{admin_email}' created successfully with password '{admin_password}'.")

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(create_admin())
