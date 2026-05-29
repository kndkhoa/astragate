# AstraGate — Project Structure

## Repository Layout

```
AstraGate1/
├── backend/                  # FastAPI application
│   ├── app/
│   │   ├── api/              # Route handlers (one file per domain)
│   │   │   ├── auth.py       # /auth/* — register, login, refresh, OAuth
│   │   │   ├── gateway.py    # /v1/* — OpenAI-compatible proxy endpoints
│   │   │   ├── billing.py    # /api/billing/* — Stripe top-up, balance, webhook
│   │   │   └── admin.py      # /admin/* — provider/model/guardrail management
│   │   ├── middleware/
│   │   │   ├── auth.py       # JWT extraction, verification, RBAC
│   │   │   └── rate_limit.py # Sliding-window rate limiting via Redis
│   │   ├── models/           # SQLAlchemy ORM models (one file per table group)
│   │   │   ├── base.py       # DeclarativeBase
│   │   │   ├── user.py       # users
│   │   │   ├── virtual_key.py# virtual_keys
│   │   │   ├── credit.py     # credit_accounts, credit_transactions
│   │   │   ├── provider.py   # providers, markup_config
│   │   │   ├── model.py      # models
│   │   │   ├── usage.py      # usage_records, provider_balance_log
│   │   │   └── guardrail.py  # guardrail_keywords, guardrail_events
│   │   ├── services/         # Business logic layer (keep routers thin)
│   │   ├── scripts/
│   │   │   └── seed.py       # Database seeding
│   │   ├── main.py           # App factory, middleware wiring, health endpoint
│   │   ├── config.py         # pydantic-settings Settings singleton
│   │   ├── database.py       # Async engine, session factory, get_db()
│   │   ├── redis_client.py   # Redis init/close/get helpers
│   │   └── logging_config.py # structlog configuration
│   ├── alembic/
│   │   └── versions/         # Migration scripts (prefix: NNNN_description.py)
│   ├── alembic.ini
│   ├── pyproject.toml
│   └── Dockerfile
│
├── frontend/                 # Next.js 14 App Router dashboard
│   ├── app/
│   │   ├── (auth)/           # Route group — unauthenticated pages
│   │   │   ├── login/
│   │   │   └── register/
│   │   ├── (marketing)/      # Route group — public landing page
│   │   ├── dashboard/        # Customer-facing pages (requires auth)
│   │   │   ├── layout.tsx    # Shared dashboard shell/nav
│   │   │   ├── page.tsx      # Overview
│   │   │   ├── keys/         # Virtual key management
│   │   │   ├── usage/        # Usage history
│   │   │   └── billing/      # Credit balance & top-up
│   │   ├── admin/            # Admin-only pages (requires admin role)
│   │   │   ├── layout.tsx
│   │   │   ├── page.tsx      # Admin overview
│   │   │   ├── providers/
│   │   │   ├── models/
│   │   │   ├── guardrails/
│   │   │   └── customers/
│   │   ├── layout.tsx        # Root layout (Inter font, metadata)
│   │   └── globals.css
│   ├── components/
│   │   ├── ui/               # Low-level Radix-based primitives (Button, Dialog, etc.)
│   │   └── shared/           # Composite components reused across pages
│   ├── lib/
│   │   ├── api.ts            # Fetch wrapper with JWT injection & 401 refresh
│   │   ├── auth.ts           # Token storage, decode, isAuthenticated, isAdmin
│   │   └── utils.ts          # cn() Tailwind class merger
│   ├── public/
│   ├── next.config.js
│   ├── tailwind.config.ts
│   └── tsconfig.json
│
├── docs/epics/               # SDLC artifacts per epic (PRD, tech design, test plans)
├── docker-compose.yml        # Full stack: api, dashboard, litellm, postgres, redis
└── .env.example              # Required environment variable template
```

## Architectural Patterns

### Backend
- **Thin routers, fat services**: Route handlers in `api/` validate input and delegate to `services/`. Business logic does not belong in routers.
- **Dependency injection**: Use FastAPI `Depends()` for `get_db()`, auth, and other shared dependencies.
- **One model file per domain**: Add new ORM models to the appropriate existing file or create a new one following the same pattern. Import all models in `models/__init__.py` so Alembic can detect them.
- **Migrations**: Every schema change requires an Alembic migration. Never modify `0001_initial_schema.py`; always create a new revision.
- **Services directory**: `app/services/` is where cross-cutting business logic lives (e.g., credit deduction, key validation, guardrail checking). Keep this layer free of HTTP concerns.

### Frontend
- **App Router conventions**: Each route is a `page.tsx`. Shared layout (nav, sidebar) goes in `layout.tsx` at the appropriate route group level.
- **API calls**: Always use the helpers from `lib/api.ts` (`get`, `post`, `put`, `del`). Never call `fetch` directly in components.
- **Auth checks**: Use `isAuthenticated()` and `isAdmin()` from `lib/auth.ts` for client-side guards. Server-side enforcement is handled by the backend JWT middleware.
- **Styling**: Use `cn()` from `lib/utils.ts` for all conditional class merging. Prefer Tailwind utilities; avoid inline styles.
- **Components**: Generic, reusable primitives go in `components/ui/`. Page-specific or multi-primitive composites go in `components/shared/`.

## API Route Prefixes

| Prefix         | Router file    | Audience        |
|----------------|----------------|-----------------|
| `/auth`        | `api/auth.py`  | Public          |
| `/v1`          | `api/gateway.py` | Authenticated customers |
| `/api/billing` | `api/billing.py` | Authenticated customers |
| `/admin`       | `api/admin.py` | Admin role only |
| `/health`      | `main.py`      | Public          |
