# AstraGate — Backend (FastAPI)

This directory contains the Python/FastAPI backend for AstraGate.

## Tech Stack

- **Python 3.11+**
- **FastAPI** — async web framework
- **SQLAlchemy 2 (async)** + **asyncpg** — PostgreSQL ORM
- **Alembic** — database migrations
- **httpx** — async HTTP client (for LiteLLM Proxy calls)
- **redis-py (async)** — Redis client
- **python-jose** — JWT handling
- **bcrypt** — password hashing
- **stripe** — Stripe SDK
- **resend** — transactional email

## Project Layout (to be created in Task 2)

```
backend/
├── app/
│   ├── main.py              # Application factory
│   ├── api/
│   │   ├── gateway.py       # /v1/* proxy endpoints
│   │   ├── auth.py          # /auth/* endpoints
│   │   ├── billing.py       # /api/billing/* endpoints
│   │   └── admin.py         # /admin/* endpoints
│   ├── middleware/
│   │   ├── auth.py          # Virtual Key validation
│   │   └── rate_limit.py    # Per-key rate limiting
│   ├── services/
│   │   ├── credit.py        # Hold / settle credit
│   │   ├── markup.py        # Markup resolution
│   │   ├── guardrail.py     # Keyword scanning
│   │   ├── provider_balance.py
│   │   ├── litellm_client.py
│   │   ├── email.py         # Resend wrapper
│   │   └── post_process.py  # Background task
│   ├── models/              # SQLAlchemy ORM models
│   ├── schemas/             # Pydantic request/response schemas
│   └── scripts/
│       └── seed.py          # Database seed script
├── alembic/                 # Alembic migration environment
├── pyproject.toml
└── Dockerfile
```

## Getting Started

```bash
# From the repo root:
make dev        # Start all Docker services
make migrate    # Run Alembic migrations
make seed       # Seed default providers, models, and markup config
```
