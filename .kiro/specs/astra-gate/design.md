# Design Document

## Overview

AstraGate là một LLM API Gateway SaaS được xây dựng theo mô hình **wrapper layer** đứng trước LiteLLM Proxy. Toàn bộ logic gọi Provider được ủy quyền cho LiteLLM Proxy; AstraGate chỉ xử lý business logic: xác thực, billing, markup, guardrails, và dashboard.

**Tech stack:**
- **Backend (API Gateway):** Python + FastAPI
- **Background tasks:** FastAPI BackgroundTasks (Phase 1), Celery (khi cần scale)
- **Database:** PostgreSQL 15
- **Cache / Rate-limit store:** Redis 7
- **LiteLLM Proxy:** Docker container, internal network only
- **Frontend (Dashboard):** Next.js 14 (App Router) + Tailwind CSS + shadcn/ui
- **Payments:** Stripe Checkout + Stripe Webhooks
- **Email:** Resend (transactional email)
- **Deployment:** Docker Compose (Phase 1, 5 services trên `internal` bridge network), Kubernetes-ready — xem mục [Deployment Architecture](#deployment-architecture)

---

## Architecture

### High-Level System Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        Internet (HTTPS)                         │
└──────────────────────┬──────────────────────────────────────────┘
                       │
          ┌────────────▼────────────┐
          │     AstraGate API       │  FastAPI  :8000
          │  (Gateway + Business)   │
          └──┬──────────┬───────────┘
             │          │
    Internal │          │ Internal
    Network  │          │ Network
             │          │
  ┌──────────▼──┐   ┌───▼──────────────┐
  │ LiteLLM     │   │   PostgreSQL      │
  │ Proxy :4000 │   │   (primary DB)    │
  └──────┬──────┘   └───────────────────┘
         │
         │ HTTPS (outbound)          ┌───────────────────┐
         ├──────────────────────────►│  Groq / DeepSeek  │
         ├──────────────────────────►│  Gemini / OpenAI  │
         └──────────────────────────►│  Anthropic / etc. │
                                     └───────────────────┘

  ┌──────────────────┐   ┌───────────────────┐
  │  Redis :6379     │   │  Next.js Dashboard │  :3000
  │  (cache + RL)    │   │  (Customer+Admin)  │
  └──────────────────┘   └───────────────────┘
```

### Request Processing Pipeline

```
Customer Request (HTTPS)
        │
        ▼
[1] Auth Middleware
    └─ Lookup Virtual_Key hash in PostgreSQL
    └─ 401 if invalid/revoked
        │
        ▼
[2] Credit Pre-check
    └─ Estimate max_cost = max_tokens × price_per_token × (1 + markup_rate)
    └─ 402 if Credit_Balance < max_cost
    └─ Hold (reserve) estimated credit in Redis
        │
        ▼
[3] Guardrail — Input
    └─ Scan prompt against banned keyword list (from DB, cached in Redis)
    └─ 400 if violation detected
        │
        ▼
[4] Provider Balance Check
    └─ Check if target Provider has Hard_Stop active
    └─ 503 if Hard_Stop and no fallback
        │
        ▼
[5] Forward to LiteLLM Proxy
    └─ HTTP POST to http://litellm:4000/v1/chat/completions
    └─ Stream SSE chunks back to Customer if stream=true
    └─ 504 if LiteLLM timeout > 30s
        │
        ▼
[6] Guardrail — Output  (non-streaming only)
    └─ Scan response against banned keyword list
    └─ Replace with error message if violation
        │
        ▼
[7] Post-processing (BackgroundTask)
    └─ Settle credit: actual_cost × (1+markup_rate), release hold
    └─ Deduct Provider_Balance by actual base_cost
    └─ Write Usage_Record to PostgreSQL
    └─ Check Provider_Balance thresholds → trigger alerts if needed
        │
        ▼
Customer Response
```

---

## Components and Interfaces

### 1. AstraGate API (FastAPI)

Entry point cho tất cả traffic từ Customer. Xử lý toàn bộ business logic trước và sau khi gọi LiteLLM Proxy.

**Modules:**
- `api/gateway.py` — proxy endpoints `/v1/chat/completions`, `/v1/embeddings`, `/v1/models`
- `api/auth.py` — Virtual Key management endpoints
- `api/billing.py` — credit top-up, Stripe webhook handler
- `api/admin.py` — admin-only endpoints (customers, providers, markup, guardrails)
- `middleware/auth.py` — Virtual Key validation middleware
- `middleware/rate_limit.py` — per-key rate limiting via Redis
- `services/credit.py` — hold/settle credit logic
- `services/markup.py` — markup resolution (model → provider → global)
- `services/guardrail.py` — keyword scanning
- `services/provider_balance.py` — balance deduction + threshold checks
- `services/litellm_client.py` — HTTP client to LiteLLM Proxy (httpx async)
- `services/email.py` — Resend email notifications

### 2. LiteLLM Proxy

Chạy như Docker container trên internal network. Không expose ra internet.

**Cấu hình (`litellm_config.yaml`):**
- Danh sách model và Provider API key
- Fallback chains per model
- Exact cache (Redis backend)
- Timeout settings

AstraGate giao tiếp với LiteLLM Proxy qua `http://litellm:4000` (Docker internal DNS).

### 3. PostgreSQL Database

Primary store cho tất cả persistent data. Xem Data Models section bên dưới.

### 4. Redis

Dùng cho:
- **Credit hold:** key `credit_hold:{request_id}` với TTL 60s
- **Rate limiting:** sliding window counter per Virtual_Key
- **Guardrail cache:** banned keyword list (invalidated on update)
- **Provider status cache:** Hard_Stop state per provider (TTL 30s)
- **LiteLLM Exact Cache:** managed by LiteLLM Proxy directly

### 5. Next.js Dashboard

Single Next.js app phục vụ cả Customer Dashboard và Admin Dashboard, phân biệt bằng role trong JWT.

**Routes:**
- `/` — landing page + login/signup
- `/dashboard/*` — Customer area (Overview, API Keys, Usage, Billing)
- `/admin/*` — Admin area (Overview, Customers, Providers, Models, Guardrails, Settings)

---

## Data Models

### users

```sql
CREATE TABLE users (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email         TEXT UNIQUE NOT NULL,
  password_hash TEXT,                    -- null if OAuth only
  oauth_provider TEXT,                   -- 'google' | null
  oauth_sub      TEXT,
  role          TEXT NOT NULL DEFAULT 'customer',  -- 'customer' | 'admin'
  is_active     BOOLEAN NOT NULL DEFAULT true,
  failed_login_attempts INT NOT NULL DEFAULT 0,
  locked_until  TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### virtual_keys

```sql
CREATE TABLE virtual_keys (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID NOT NULL REFERENCES users(id),
  name          TEXT NOT NULL,
  description   TEXT,
  key_hash      TEXT UNIQUE NOT NULL,    -- SHA-256 of plaintext key
  key_prefix    TEXT NOT NULL,           -- first 8 chars for display (e.g. "ag-sk-ab")
  is_active     BOOLEAN NOT NULL DEFAULT true,
  rate_limit_rpm INT,                    -- requests per minute, null = unlimited
  last_used_at  TIMESTAMPTZ,
  total_requests BIGINT NOT NULL DEFAULT 0,
  total_tokens  BIGINT NOT NULL DEFAULT 0,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at    TIMESTAMPTZ
);
CREATE INDEX idx_virtual_keys_hash ON virtual_keys(key_hash);
CREATE INDEX idx_virtual_keys_user ON virtual_keys(user_id);
```

### credit_accounts

```sql
CREATE TABLE credit_accounts (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID UNIQUE NOT NULL REFERENCES users(id),
  balance_usd   NUMERIC(12,6) NOT NULL DEFAULT 0,
  last_topup_amount NUMERIC(10,2),
  last_topup_at TIMESTAMPTZ,
  low_balance_alert_sent_at TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### credit_transactions

```sql
CREATE TABLE credit_transactions (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID NOT NULL REFERENCES users(id),
  type            TEXT NOT NULL,   -- 'topup' | 'usage' | 'refund' | 'free_credit'
  amount_usd      NUMERIC(12,6) NOT NULL,  -- positive=credit, negative=debit
  balance_after   NUMERIC(12,6) NOT NULL,
  stripe_payment_intent_id TEXT,
  usage_record_id UUID,
  description     TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_credit_tx_user ON credit_transactions(user_id, created_at DESC);
```

### providers

```sql
CREATE TABLE providers (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name                TEXT UNIQUE NOT NULL,   -- 'groq' | 'deepseek' | 'gemini' | 'openai'
  display_name        TEXT NOT NULL,
  balance_usd         NUMERIC(12,4) NOT NULL DEFAULT 0,
  warning_threshold   NUMERIC(10,2) NOT NULL DEFAULT 10.00,
  hard_stop_threshold NUMERIC(10,2) NOT NULL DEFAULT 2.00,
  status              TEXT NOT NULL DEFAULT 'normal',  -- 'normal' | 'warning' | 'hard_stop'
  fallback_provider_id UUID REFERENCES providers(id),
  last_warning_alert_at TIMESTAMPTZ,
  hard_stop_activated_at TIMESTAMPTZ,
  is_active           BOOLEAN NOT NULL DEFAULT true,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### models

```sql
CREATE TABLE models (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_id       UUID NOT NULL REFERENCES providers(id),
  model_id          TEXT NOT NULL,           -- e.g. 'groq/llama-3.1-8b-instant'
  display_name      TEXT NOT NULL,
  input_price_per_1m  NUMERIC(10,6) NOT NULL,  -- USD per 1M input tokens (base cost)
  output_price_per_1m NUMERIC(10,6) NOT NULL,  -- USD per 1M output tokens (base cost)
  markup_rate       NUMERIC(6,4),              -- model-level markup, null = inherit
  is_active         BOOLEAN NOT NULL DEFAULT true,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(provider_id, model_id)
);
```

### markup_config

```sql
CREATE TABLE markup_config (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scope         TEXT NOT NULL,    -- 'global' | 'provider' | 'model'
  provider_id   UUID REFERENCES providers(id),
  model_id      UUID REFERENCES models(id),
  markup_rate   NUMERIC(6,4) NOT NULL DEFAULT 0.20,  -- 0.0 to 5.0
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(scope, provider_id, model_id)
);
-- Seed: INSERT one row with scope='global', markup_rate=0.20
```

### usage_records

```sql
CREATE TABLE usage_records (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  virtual_key_id  UUID NOT NULL REFERENCES virtual_keys(id),
  user_id         UUID NOT NULL REFERENCES users(id),
  model_id        UUID REFERENCES models(id),
  provider_id     UUID REFERENCES providers(id),
  model_name      TEXT NOT NULL,           -- actual model used (may differ if fallback)
  provider_name   TEXT NOT NULL,
  input_tokens    INT NOT NULL DEFAULT 0,
  output_tokens   INT NOT NULL DEFAULT 0,
  total_tokens    INT NOT NULL DEFAULT 0,
  base_cost_usd   NUMERIC(12,6) NOT NULL DEFAULT 0,   -- provider cost, no markup
  markup_rate     NUMERIC(6,4) NOT NULL DEFAULT 0,
  billed_amount_usd NUMERIC(12,6) NOT NULL DEFAULT 0, -- base_cost × (1 + markup_rate)
  latency_ms      INT,
  cache_hit       BOOLEAN NOT NULL DEFAULT false,
  is_fallback     BOOLEAN NOT NULL DEFAULT false,
  status          TEXT NOT NULL DEFAULT 'success',    -- 'success' | 'error' | 'guardrail_blocked'
  error_code      TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_usage_user_time ON usage_records(user_id, created_at DESC);
CREATE INDEX idx_usage_key_time  ON usage_records(virtual_key_id, created_at DESC);
CREATE INDEX idx_usage_provider  ON usage_records(provider_id, created_at DESC);
-- Partition by month for 90-day retention (Phase 2)
```

### provider_balance_log

```sql
CREATE TABLE provider_balance_log (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_id     UUID NOT NULL REFERENCES providers(id),
  change_type     TEXT NOT NULL,   -- 'usage_deduct' | 'manual_update'
  amount_usd      NUMERIC(12,6) NOT NULL,   -- negative=deduct, positive=topup
  balance_before  NUMERIC(12,4) NOT NULL,
  balance_after   NUMERIC(12,4) NOT NULL,
  usage_record_id UUID REFERENCES usage_records(id),
  note            TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_pbl_provider ON provider_balance_log(provider_id, created_at DESC);
```

### guardrail_keywords

```sql
CREATE TABLE guardrail_keywords (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  keyword     TEXT NOT NULL,
  scope       TEXT NOT NULL DEFAULT 'both',  -- 'input' | 'output' | 'both'
  is_active   BOOLEAN NOT NULL DEFAULT true,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### guardrail_events

```sql
CREATE TABLE guardrail_events (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  virtual_key_id  UUID REFERENCES virtual_keys(id),
  user_id         UUID REFERENCES users(id),
  direction       TEXT NOT NULL,   -- 'input' | 'output'
  keyword_matched TEXT NOT NULL,
  content_snippet TEXT,            -- first 100 chars, truncated
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## Key Design Decisions

### Credit Hold / Settle Pattern

Để tránh trường hợp Customer dùng nhiều hơn credit họ có (race condition khi concurrent requests), AstraGate dùng Redis atomic operations:

```
1. BEFORE calling LiteLLM:
   - estimated_cost = (max_tokens / 1000) × price_per_1k × (1 + markup_rate)
   - Redis: SETNX credit_hold:{request_id} {estimated_cost} EX 60
   - Atomically check & deduct from credit_accounts.balance_usd via PostgreSQL
     using SELECT ... FOR UPDATE to prevent race conditions

2. AFTER LiteLLM responds:
   - actual_cost = (actual_tokens / 1M) × price_per_1M × (1 + markup_rate)
   - UPDATE credit_accounts SET balance_usd = balance_usd - actual_cost
   - Release hold: DEL credit_hold:{request_id}
   - If actual_cost < estimated_cost → difference is automatically released
```

**Fallback nếu LiteLLM timeout/error:** hold được release, không trừ credit.

### Markup Resolution

```python
def resolve_markup_rate(model_id: UUID, provider_id: UUID) -> float:
    # 1. Check model-level markup
    if model.markup_rate is not None:
        return model.markup_rate
    # 2. Check provider-level markup
    provider_markup = markup_config.get(scope='provider', provider_id=provider_id)
    if provider_markup:
        return provider_markup.markup_rate
    # 3. Fall back to global default
    global_markup = markup_config.get(scope='global')
    return global_markup.markup_rate  # default 0.20
```

Markup rate được cache trong Redis với TTL 60s để tránh DB query mỗi request.

### Virtual Key Lookup

Virtual Key được hash bằng SHA-256 trước khi lưu. Khi Customer gửi request:

```python
key_hash = hashlib.sha256(bearer_token.encode()).hexdigest()
virtual_key = db.query(VirtualKey).filter_by(key_hash=key_hash, is_active=True).first()
```

Lookup được cache trong Redis với key `vk:{key_hash}` TTL 30s. Khi key bị revoke, cache bị invalidated ngay lập tức.

### Provider Hard Stop

Provider status được cache trong Redis (`provider_status:{provider_id}`, TTL 30s). Khi Hard Stop được kích hoạt:

```
1. UPDATE providers SET status='hard_stop', hard_stop_activated_at=now()
2. DEL provider_status:{provider_id}  ← force cache invalidation
3. Send email alert to Admin immediately
4. Log to provider_balance_log
```

Khi Admin gỡ Hard Stop (sau khi nạp tiền):
```
1. UPDATE providers SET status='normal', hard_stop_activated_at=null
2. DEL provider_status:{provider_id}
```

### Streaming (SSE) Proxy

```python
@router.post("/v1/chat/completions")
async def chat_completions(request: Request, ...):
    if body.get("stream"):
        async def stream_generator():
            async with litellm_client.stream("POST", "/v1/chat/completions", json=body) as resp:
                async for chunk in resp.aiter_bytes():
                    yield chunk
            # After stream ends, run post-processing in background
            background_tasks.add_task(post_process_usage, request_id, ...)
        return StreamingResponse(stream_generator(), media_type="text/event-stream")
    else:
        # Non-streaming: apply output guardrail before returning
        response = await litellm_client.post("/v1/chat/completions", json=body)
        check_output_guardrail(response)
        background_tasks.add_task(post_process_usage, request_id, ...)
        return response.json()
```

**Lưu ý:** Output guardrail chỉ áp dụng cho non-streaming response. Streaming response không thể scan toàn bộ output trước khi trả về — đây là trade-off chấp nhận được ở Phase 1.

### Rate Limiting

Dùng Redis sliding window algorithm:

```python
# Key: rate_limit:{virtual_key_id}:{window_start_minute}
# Value: request count
# TTL: 2 minutes

async def check_rate_limit(key_id: str, limit_rpm: int) -> bool:
    now = int(time.time())
    window = now // 60
    redis_key = f"rate_limit:{key_id}:{window}"
    count = await redis.incr(redis_key)
    if count == 1:
        await redis.expire(redis_key, 120)
    return count <= limit_rpm
```

---

## API Endpoints

### Gateway Endpoints (Customer-facing)

```
POST   /v1/chat/completions     — OpenAI-compatible chat
POST   /v1/embeddings           — OpenAI-compatible embeddings
GET    /v1/models               — List available models
GET    /health                  — Health check (public)
```

### Auth & Account Endpoints

```
POST   /auth/register           — Email/password signup
POST   /auth/login              — Login → JWT
POST   /auth/oauth/google       — Google OAuth callback
POST   /auth/logout             — Invalidate session
GET    /auth/me                 — Current user info
```

### Customer API Endpoints

```
GET    /api/keys                — List virtual keys
POST   /api/keys                — Create virtual key
DELETE /api/keys/{id}           — Revoke virtual key

GET    /api/usage               — Usage records (paginated, filterable)
GET    /api/usage/summary       — Aggregated stats (daily/weekly/monthly)

GET    /api/billing/balance     — Current credit balance
POST   /api/billing/topup       — Create Stripe Checkout session
GET    /api/billing/transactions — Transaction history
POST   /api/billing/webhook     — Stripe webhook (public, signature-verified)
```

### Admin API Endpoints

```
GET    /admin/overview          — System stats
GET    /admin/customers         — List all customers
GET    /admin/customers/{id}    — Customer detail + usage

GET    /admin/providers         — List providers + balances
PUT    /admin/providers/{id}/balance    — Update provider balance manually
PUT    /admin/providers/{id}/thresholds — Update warning/hard_stop thresholds
POST   /admin/providers/{id}/release-hard-stop — Manually release Hard Stop

GET    /admin/models            — List models + markup
PUT    /admin/models/{id}/markup — Set model-level markup
PUT    /admin/providers/{id}/markup — Set provider-level markup
PUT    /admin/markup/global     — Set global default markup

GET    /admin/guardrails        — List keywords
POST   /admin/guardrails        — Add keyword
DELETE /admin/guardrails/{id}   — Remove keyword

GET    /admin/usage/export      — Export CSV
```

---

## LiteLLM Proxy Configuration

File `litellm_config.yaml` được mount vào LiteLLM container:

```yaml
model_list:
  # Primary: Free/cheap providers (Phase 1 priority)
  - model_name: llama-3.1-8b
    litellm_params:
      model: groq/llama-3.1-8b-instant
      api_key: os.environ/GROQ_API_KEY

  - model_name: deepseek-chat
    litellm_params:
      model: deepseek/deepseek-chat
      api_key: os.environ/DEEPSEEK_API_KEY

  - model_name: gemini-flash
    litellm_params:
      model: gemini/gemini-1.5-flash
      api_key: os.environ/GEMINI_API_KEY

  # Fallback chains
  - model_name: llama-3.1-8b
    litellm_params:
      model: deepseek/deepseek-chat   # fallback if Groq fails
      api_key: os.environ/DEEPSEEK_API_KEY

router_settings:
  routing_strategy: simple-shuffle
  num_retries: 2
  timeout: 25                          # AstraGate timeout is 30s, LiteLLM gets 25s
  retry_after: 0

cache:
  type: redis
  host: redis
  port: 6379
  ttl: 3600                            # 1 hour exact cache TTL

litellm_settings:
  success_callback: []                 # AstraGate handles logging, not LiteLLM
  drop_params: true                    # ignore unknown params gracefully
```

**Quan trọng:** LiteLLM Proxy chạy với `LITELLM_MASTER_KEY` riêng (không phải Virtual Key của Customer). AstraGate dùng master key này để gọi LiteLLM nội bộ.

---

## Deployment Architecture

Toàn bộ hệ thống được đóng gói và chạy bằng **Docker Compose** (Phase 1), gồm **5 service** trên một bridge network nội bộ tên `internal`. Chỉ **2 service** được map port ra host (`api:8000`, `dashboard:3000`); 3 service hạ tầng còn lại (`litellm`, `postgres`, `redis`) **không expose** ra ngoài và chỉ truy cập được qua DNS nội bộ của Docker.

### Deployment Topology

```
                         INTERNET / HOST
                               │
        ┌──────────────────────┴───────────────────────┐
        │                                               │
  :3000 │ (HTTP)                                  :8000 │ (HTTP)
        ▼                                               ▼
┌────────────────┐                          ┌────────────────────┐
│   dashboard    │   browser → API (JWT)    │        api         │
│  Next.js 14    │ ───────────────────────► │      FastAPI       │
│ (multi-stage,  │                          │ (python:3.11-slim) │
│  standalone)   │                          └─────────┬──────────┘
└────────────────┘                                    │
                          ┌───────────────┬───────────┼────────────────┐
                          ▼               ▼           ▼                
                   ┌────────────┐  ┌────────────┐  ┌──────────────┐    
                   │  postgres  │  │   redis    │  │   litellm    │ ──► Groq
                   │ 15-alpine  │  │  7-alpine  │  │  :4000       │ ──► DeepSeek
                   │  :5432     │  │  :6379     │  │ (no host port)│ ──► Gemini
                   └────────────┘  └────────────┘  └──────────────┘    (outbound
                    (no host port)  (no host port)                      HTTPS)
                   ══════════════ internal bridge network ══════════════
```

### Service Inventory

| Service | Image / Build | Host port | Persistence | Vai trò |
|---|---|---|---|---|
| `api` | build `./backend` (python:3.11-slim) | **8000** | — | FastAPI gateway + business logic |
| `dashboard` | build `./frontend` (node:20-alpine, multi-stage) | **3000** | — | Next.js dashboard (Customer + Admin) |
| `litellm` | `ghcr.io/berriai/litellm:main-latest` | *none* | config volume (ro) | LLM proxy → providers |
| `postgres` | `postgres:15-alpine` | *none* | `pgdata` | Primary database |
| `redis` | `redis:7-alpine` | *none* | `redisdata` | Cache + rate-limit store |

### Startup Ordering & Health

- `api` `depends_on` healthcheck: chờ `postgres` (`pg_isready`) và `redis` (`redis-cli ping`) ở trạng thái **healthy**, và `litellm` ở trạng thái **started** trước khi khởi động.
- `dashboard` `depends_on` `api`.
- Tất cả service đặt `restart: unless-stopped`.

### Image Build Strategy

- **Backend** (`backend/Dockerfile`): single-stage — `pip install -e .`, chạy `uvicorn app.main:app` trên `0.0.0.0:8000`.
- **Frontend** (`frontend/Dockerfile`): multi-stage (`deps` → `builder` → `runner`), dùng Next.js **standalone output** để image production gọn, chạy `node server.js` trên port 3000.

### docker-compose.yml

```yaml
version: "3.9"

services:
  api:
    build: ./backend
    ports: ["8000:8000"]
    environment:
      DATABASE_URL: postgresql+asyncpg://astragate:${POSTGRES_PASSWORD}@postgres:5432/astragate
      REDIS_URL: redis://redis:6379
      LITELLM_URL: http://litellm:4000
      LITELLM_MASTER_KEY: ${LITELLM_MASTER_KEY}
      STRIPE_SECRET_KEY: ${STRIPE_SECRET_KEY}
      STRIPE_WEBHOOK_SECRET: ${STRIPE_WEBHOOK_SECRET}
      RESEND_API_KEY: ${RESEND_API_KEY}
      JWT_SECRET: ${JWT_SECRET}
      JWT_REFRESH_SECRET: ${JWT_REFRESH_SECRET}
      DB_ENCRYPTION_KEY: ${DB_ENCRYPTION_KEY}
    depends_on:
      postgres: { condition: service_healthy }
      redis: { condition: service_healthy }
      litellm: { condition: service_started }
    networks: [internal]
    restart: unless-stopped

  litellm:
    image: ghcr.io/berriai/litellm:main-latest
    volumes: ["./litellm/litellm_config.yaml:/app/config.yaml"]
    environment:
      GROQ_API_KEY: ${GROQ_API_KEY}
      DEEPSEEK_API_KEY: ${DEEPSEEK_API_KEY}
      GEMINI_API_KEY: ${GEMINI_API_KEY}
      LITELLM_MASTER_KEY: ${LITELLM_MASTER_KEY}
    command: ["--config", "/app/config.yaml", "--port", "4000"]
    networks: [internal]   # NO ports mapping — NOT exposed to internet
    restart: unless-stopped

  dashboard:
    build: ./frontend
    ports: ["3000:3000"]
    environment:
      NEXT_PUBLIC_API_URL: ${NEXT_PUBLIC_API_URL:-http://localhost:8000}
    depends_on: [api]
    networks: [internal]
    restart: unless-stopped

  postgres:
    image: postgres:15-alpine
    volumes: ["pgdata:/var/lib/postgresql/data"]
    environment:
      POSTGRES_USER: astragate
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: astragate
    networks: [internal]
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U astragate -d astragate"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    volumes: ["redisdata:/data"]
    networks: [internal]
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

networks:
  internal:
    driver: bridge

volumes:
  pgdata:
  redisdata:
```

### Configuration & Secrets

Tất cả secret được inject qua biến môi trường từ file `.env` (xem `.env.example`), không hardcode trong image: `POSTGRES_PASSWORD`, `JWT_SECRET`, `JWT_REFRESH_SECRET`, `DB_ENCRYPTION_KEY`, `LITELLM_MASTER_KEY`, provider keys (`GROQ_API_KEY`, `DEEPSEEK_API_KEY`, `GEMINI_API_KEY`), `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `RESEND_API_KEY`.

### Production Note (chưa có trong Phase 1)

Compose hiện chưa có lớp reverse proxy / TLS termination; `api` và `dashboard` đang map trực tiếp ra host bằng HTTP. Khi triển khai production ra internet cần bổ sung reverse proxy + HTTPS (Caddy/Nginx) phía trước, như đã ghi trong mục Security Design.

---

## Frontend Structure (Next.js)

```
frontend/
├── app/
│   ├── (marketing)/
│   │   └── page.tsx              — Landing page
│   ├── (auth)/
│   │   ├── login/page.tsx
│   │   └── register/page.tsx
│   ├── dashboard/
│   │   ├── layout.tsx            — Customer layout + nav
│   │   ├── page.tsx              — Overview (balance, recent usage)
│   │   ├── keys/page.tsx         — API Keys management
│   │   ├── usage/page.tsx        — Usage charts + table
│   │   └── billing/page.tsx      — Top-up + transaction history
│   └── admin/
│       ├── layout.tsx            — Admin layout + nav
│       ├── page.tsx              — System overview
│       ├── customers/page.tsx
│       ├── providers/page.tsx    — Balance + thresholds + Hard Stop
│       ├── models/page.tsx       — Markup configuration
│       └── guardrails/page.tsx
├── components/
│   ├── ui/                       — shadcn/ui components
│   ├── charts/                   — Recharts wrappers
│   └── shared/
└── lib/
    ├── api.ts                    — API client (fetch wrapper)
    └── auth.ts                   — JWT handling
```

---

## Security Design

| Concern | Solution |
|---|---|
| Virtual Key storage | SHA-256 hash only; plaintext shown once at creation |
| Password storage | bcrypt, cost factor 12 |
| Provider API keys | AES-256 encrypted at rest in PostgreSQL (via pgcrypto) |
| Transport | HTTPS/TLS 1.2+ enforced at reverse proxy (Caddy/Nginx) |
| Stripe webhooks | Verify `Stripe-Signature` header before processing |
| Admin access | Separate JWT role claim `admin`; middleware rejects non-admin |
| Login brute force | Lock account after 10 failed attempts in 15 min (Redis counter) |
| LiteLLM exposure | Internal Docker network only; no public port binding |
| JWT | Short-lived access token (15 min) + refresh token (7 days) |

---

## Email Notifications (Resend)

| Trigger | Recipient | Template |
|---|---|---|
| Registration complete | Customer | Welcome + Quick Start guide |
| Credit balance < 20% | Customer | Low balance warning + top-up link |
| Provider balance < warning_threshold | Admin | Provider warning (max 1/hour per provider) |
| Provider Hard Stop activated | Admin | Hard Stop alert (immediate) |
| Credit top-up successful | Customer | Payment confirmation |

---

## Phase 1 Scope Boundaries

**In scope (MVP):**
- Virtual Key CRUD + auth
- Credit prepaid + Stripe Checkout
- Markup engine (3-level hierarchy)
- Provider balance tracking + Hard Stop
- Basic keyword guardrails
- Exact cache via LiteLLM
- Fallback routing via LiteLLM config
- Customer Dashboard (4 pages)
- Admin Dashboard (6 pages)
- OpenAI-compatible API
- Email notifications via Resend

**Out of scope (Phase 2+):**
- Team/organization accounts
- Custom model fine-tuning
- Advanced AI guardrails (toxicity classifiers, PII detection)
- Semantic cache
- Usage-based billing (postpaid)
- Multi-region deployment
- Audit log export
- API rate limiting by IP (only by Virtual Key in Phase 1)
- Automatic Provider balance sync (requires Provider API support)
