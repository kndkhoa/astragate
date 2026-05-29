# AstraGate — Tech Stack & Build System

## Backend

- **Language**: Python 3.11+
- **Framework**: FastAPI 0.115 with async/await throughout
- **ORM**: SQLAlchemy 2.0 (async) with `asyncpg` driver
- **Migrations**: Alembic — migration files live in `backend/alembic/versions/`
- **Database**: PostgreSQL 15
- **Cache / Rate Limiting**: Redis 7 (`redis[hiredis]`)
- **LLM Proxy**: LiteLLM (internal Docker service, port 4000, never public)
- **Auth**: JWT via `python-jose` — access tokens (15 min), refresh tokens (7 days), HS256
- **Payments**: Stripe (`stripe` SDK)
- **Email**: Resend (`resend` SDK)
- **Encryption**: AES for sensitive DB fields (`DB_ENCRYPTION_KEY`)
- **Logging**: `structlog` — structured JSON logs, request_id bound per request
- **Config**: `pydantic-settings` — all config from environment variables, `.env` file supported
- **Build backend**: `hatchling`

## Frontend

- **Framework**: Next.js 14 (App Router)
- **Language**: TypeScript 5.5
- **Styling**: Tailwind CSS 3.4 + `tailwind-merge` + `clsx` (use `cn()` from `lib/utils.ts`)
- **UI Components**: Radix UI primitives (`@radix-ui/*`)
- **Icons**: `lucide-react`
- **Charts**: `recharts`
- **HTTP Client**: Custom `apiRequest` wrapper in `lib/api.ts` — handles JWT injection and silent token refresh on 401

## Infrastructure

- **Containerisation**: Docker Compose (`docker-compose.yml`) — services: `api`, `dashboard`, `litellm`, `postgres`, `redis`
- **Internal network**: All services communicate on the `internal` bridge network; only `api` (8000) and `dashboard` (3000) are port-mapped to the host

## Common Commands

### Backend (run from `backend/`)
```bash
# Install dependencies
pip install -e .

# Run dev server
uvicorn app.main:app --reload --port 8000

# Run migrations
alembic upgrade head

# Create a new migration
alembic revision --autogenerate -m "description"

# Seed database
python -m app.scripts.seed
```

### Frontend (run from `frontend/`)
```bash
npm install
npm run dev       # development server on port 3000
npm run build     # production build
npm run lint      # ESLint
```

### Docker (run from repo root)
```bash
docker compose up --build        # start all services
docker compose up -d             # start detached
docker compose logs -f api       # tail API logs
docker compose down              # stop all
```

## Key Conventions

- All backend DB operations are **async** — never use synchronous SQLAlchemy calls.
- Use `get_db()` as a FastAPI dependency to obtain an `AsyncSession`; it auto-commits on success and rolls back on exception.
- All settings are accessed via the `settings` singleton from `app.config` — never read `os.environ` directly.
- Error responses follow the shape `{ "error": { "code": str, "message": str, "type": str } }` — use the existing exception handlers in `main.py`, don't return raw strings.
- Log with `structlog` (`get_logger(__name__)`), not `print` or the stdlib `logging` module directly.
