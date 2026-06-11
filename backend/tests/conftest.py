import os
import socket
import subprocess
import time
import pytest
import asyncio
from decimal import Decimal
import uuid
import bcrypt

# Set environment variables BEFORE importing app modules
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///app/astragate_test.db"
os.environ["APP_ENV"] = "test"

from app.models.base import Base
from app.database import engine, AsyncSessionLocal
from app.models.provider import Provider
from app.models.model import Model, MarkupConfig
from app.models.user import User
from app.models.credit import CreditAccount

def is_port_open(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0

async def init_test_db():
    # Create all tables on SQLite
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    # Seed data
    async with AsyncSessionLocal() as session:
        # 1. Insert providers
        providers = [
            Provider(id=uuid.UUID("40c3aa85-6a57-4f2c-904f-f9ae09a36ca6"), name="groq", display_name="Groq", balance_usd=Decimal("100.00")),
            Provider(id=uuid.UUID("b0062149-3baa-46e2-9756-b70b8e107d7d"), name="deepseek", display_name="DeepSeek", balance_usd=Decimal("100.00")),
            Provider(id=uuid.UUID("494edb07-da7f-4bb6-82bc-98781b124e32"), name="gemini", display_name="Google Gemini", balance_usd=Decimal("100.00")),
        ]
        session.add_all(providers)
        await session.flush()
        
        # Set fallback chains
        providers[0].fallback_provider_id = providers[1].id
        providers[1].fallback_provider_id = providers[2].id
        
        # 2. Insert models
        models = [
            Model(id=uuid.uuid4(), provider_id=providers[0].id, model_id="groq/llama-3.1-8b-instant", display_name="Llama 3.1 8B Instant", input_price_per_1m=Decimal("0.05"), output_price_per_1m=Decimal("0.08")),
            Model(id=uuid.uuid4(), provider_id=providers[1].id, model_id="deepseek/deepseek-chat", display_name="DeepSeek Chat", input_price_per_1m=Decimal("0.14"), output_price_per_1m=Decimal("0.28")),
            Model(id=uuid.uuid4(), provider_id=providers[2].id, model_id="gemini/gemini-1.5-flash", display_name="Gemini 1.5 Flash", input_price_per_1m=Decimal("0.075"), output_price_per_1m=Decimal("0.30")),
        ]
        session.add_all(models)
        
        # 3. Insert markup config
        markup = MarkupConfig(id=uuid.uuid4(), scope="global", markup_rate=Decimal("0.20"))
        session.add(markup)
        
        # 4. Insert admin user
        # admin password is admin123
        pw_hash = bcrypt.hashpw(b"admin123", bcrypt.gensalt()).decode("utf-8")
        admin = User(id=uuid.uuid4(), email="admin@astragate.io", password_hash=pw_hash, role="admin")
        session.add(admin)
        await session.flush()
        
        # admin credit account
        admin_account = CreditAccount(id=uuid.uuid4(), user_id=admin.id, balance_usd=Decimal("1000.00"))
        session.add(admin_account)
        
        await session.commit()

@pytest.fixture(scope="session", autouse=True)
def test_server():
    # Ensure any old test database is deleted first
    db_path = "/app/astragate_test.db"
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except Exception:
            pass

    # 1. Setup DB
    asyncio.run(init_test_db())
    
    # 2. Start uvicorn server on port 8001
    env = os.environ.copy()
    env["DATABASE_URL"] = "sqlite+aiosqlite:///app/astragate_test.db"
    env["APP_ENV"] = "test"
    
    proc = subprocess.Popen(
        ["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8001"],
        env=env,
        cwd="/app",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    
    # Wait for server to start
    for _ in range(30):
        if is_port_open(8001):
            break
        time.sleep(0.2)
    else:
        # Capture some stderr output for debugging if it fails
        stderr_output = proc.stderr.read(1000).decode("utf-8") if proc.stderr else "No stderr"
        proc.terminate()
        raise RuntimeError(f"Uvicorn test server failed to start on port 8001. Stderr: {stderr_output}")
        
    # Override BASE_URL in test_e2e_integration
    try:
        from tests import test_e2e_integration
        test_e2e_integration.BASE_URL = "http://127.0.0.1:8001"
    except ImportError:
        pass
        
    yield
    
    # Teardown
    proc.terminate()
    proc.wait()
    
    # Delete test database file
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except Exception:
            pass
