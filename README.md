# AstraGate

A self-hosted LLM API gateway that provides a single OpenAI-compatible endpoint in front of multiple LLM providers (Groq, DeepSeek, Gemini). Customers prepay with credits and consume them per request with configurable markup.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                         Docker Network (internal)                     │
│                                                                      │
│  ┌─────────────┐     ┌────────────────┐     ┌───────────────────┐  │
│  │  Dashboard   │     │   AstraGate    │     │   LiteLLM Proxy   │  │
│  │  (Next.js)   │────▶│   API (FastAPI) │────▶│  (not exposed)    │  │
│  │  :3000       │     │   :8000        │     │  :4000 internal   │  │
│  └─────────────┘     └────────────────┘     └───────────────────┘  │
│                              │   │                     │             │
│                              │   │                     ▼             │
│                              │   │            ┌──────────────────┐  │
│                              │   │            │  Groq / DeepSeek │  │
│                              │   │            │  / Gemini APIs   │  │
│                              ▼   ▼            └──────────────────┘  │
│                       ┌────────┐ ┌───────┐                          │
│                       │Postgres│ │ Redis │                          │
│                       │  :5432 │ │ :6379 │                          │
│                       └────────┘ └───────┘                          │
└──────────────────────────────────────────────────────────────────────┘
```

**Key design decisions:**
- LiteLLM Proxy is **never** exposed to the internet — only reachable on the internal Docker network.
- Credit deduction is **atomic** (SELECT ... FOR UPDATE) to prevent race conditions under concurrent load.
- Provider balances are tracked separately from customer credits.

## Features

- **Unified API** — OpenAI-compatible `/v1/chat/completions`, `/v1/embeddings`, `/v1/models`
- **Virtual Keys** — Scoped API keys with optional per-key rate limits (RPM)
- **Prepaid Credits** — Stripe Checkout top-up, automatic billing per request
- **3-Level Markup** — Model → Provider → Global, resolved with Redis caching
- **Provider Management** — Balance tracking, warning/hard-stop thresholds, automatic fallback
- **Guardrails** — Keyword-based content filtering on input and output
- **Admin Dashboard** — Providers, models, markup, guardrails, customer management
- **Customer Dashboard** — Balance, usage history, virtual keys, quick start guide

## Quick Start (Local Development)

### Prerequisites

- Docker & Docker Compose
- Node.js 18+ (for frontend dev outside Docker)
- Python 3.11+ (for backend dev outside Docker)
- k6 (optional, for load testing)

### 1. Clone and configure environment

```bash
git clone <repo-url> astragate
cd astragate
cp .env.example .env
# Edit .env with your API keys (Groq, DeepSeek, Gemini, Stripe, Resend)
```

### 2. Start all services with Docker Compose

```bash
docker compose up --build
```

This starts: `api` (8000), `dashboard` (3000), `litellm` (4000 internal), `postgres` (5432), `redis` (6379).

### 3. Run migrations and seed data

```bash
# In another terminal
docker compose exec api alembic upgrade head
docker compose exec api python -m app.scripts.seed
```

### 4. Access the application

- **Dashboard**: http://localhost:3000
- **API**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs
- **Health Check**: http://localhost:8000/health

### 5. Register and make your first API call

```bash
# Register a user (gets $1.00 free credit + default Virtual Key)
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "test@example.com", "password": "mypassword123"}'

# Use the returned default_key to make an API call
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer ag-sk-your-key-here" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama-3.1-8b",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

## Development (without Docker)

### Backend

```bash
cd backend
pip install -e .
# Set DATABASE_URL to a local postgres or sqlite for dev:
# export DATABASE_URL="sqlite+aiosqlite:///./astragate.db"
alembic upgrade head
python -m app.scripts.seed
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev  # http://localhost:3000
```

## Environment Variables Reference

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL async connection URL | `postgresql+asyncpg://...` |
| `POSTGRES_PASSWORD` | PostgreSQL password | — |
| `REDIS_URL` | Redis connection URL | `redis://redis:6379` |
| `LITELLM_URL` | Internal LiteLLM Proxy URL | `http://litellm:4000` |
| `LITELLM_MASTER_KEY` | Auth key for LiteLLM Proxy | — |
| `GROQ_API_KEY` | Groq provider API key | — |
| `DEEPSEEK_API_KEY` | DeepSeek provider API key | — |
| `GEMINI_API_KEY` | Google Gemini API key | — |
| `JWT_SECRET` | Access token signing secret | — |
| `JWT_REFRESH_SECRET` | Refresh token signing secret | — |
| `GOOGLE_CLIENT_ID` | Google OAuth client ID | — |
| `STRIPE_SECRET_KEY` | Stripe API secret key | — |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook signature secret | — |
| `RESEND_API_KEY` | Resend email API key | — |
| `DB_ENCRYPTION_KEY` | AES-256 key for encrypting provider credentials | — |
| `NEXT_PUBLIC_API_URL` | Public-facing API URL for frontend | `http://localhost:8000` |
| `APP_ENV` | Environment (`development` / `production`) | `development` |
| `LOG_LEVEL` | Logging level | `INFO` |

## Adding a New LLM Provider

1. Add the provider's API key to `.env` and `docker-compose.yml` environment section for `litellm`
2. Edit `litellm/litellm_config.yaml` to add model entries under the new provider
3. Run `docker compose restart litellm`
4. Add the provider to the database via seed script or admin API:
   ```bash
   # Via Admin API
   curl -X POST http://localhost:8000/admin/providers \
     -H "Authorization: Bearer <admin_token>" \
     -d '{"name": "newprovider", "display_name": "New Provider", "balance_usd": 50.00}'
   ```
5. Add model entries linking to the new provider

## Running Tests

```bash
cd backend

# Unit tests
pytest tests/ -v

# E2E integration tests (requires running services)
pytest tests/test_e2e_integration.py -v

# Load test (requires k6 installed)
k6 run tests/load_test.js
```

## API Route Map

| Prefix | Router | Audience |
|--------|--------|----------|
| `/auth` | `api/auth.py` | Public |
| `/v1` | `api/gateway.py` | Virtual Key auth |
| `/api/billing` | `api/billing.py` | JWT auth (customers) |
| `/api/keys` | `api/keys.py` | JWT auth (customers) |
| `/api/usage` | `api/usage.py` | JWT auth (customers) |
| `/admin` | `api/admin.py` | JWT auth (admin only) |
| `/health` | `main.py` | Public |

## License

Proprietary — All rights reserved.
