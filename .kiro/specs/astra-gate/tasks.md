# Implementation Plan

## Overview

45 tasks across 12 milestones, ordered by dependency. Each task maps to one or more Requirements. The critical path is: Infrastructure (M1) → Auth (M2) → Virtual Keys (M3) → Markup + Providers (M4) → Gateway Pipeline (M5) → Billing (M6) → Dashboard (M9) → Onboarding (M10).

## Task Dependency Graph

```json
{
  "waves": [
    {
      "wave": 1,
      "tasks": [1, 2, 3, 4, 5],
      "description": "Foundation: Docker, FastAPI, PostgreSQL, LiteLLM, Next.js"
    },
    {
      "wave": 2,
      "tasks": [6, 7, 8, 9],
      "description": "Auth: register/login, OAuth, JWT middleware, auth UI"
    },
    {
      "wave": 3,
      "tasks": [10, 11, 12, 13],
      "description": "Virtual Keys + Markup service (parallel)"
    },
    {
      "wave": 4,
      "tasks": [14, 15, 16],
      "description": "Admin markup + provider management endpoints + UI"
    },
    {
      "wave": 5,
      "tasks": [17, 18, 19, 20, 21],
      "description": "Gateway pipeline services: credit hold, guardrail, rate limit, provider check, LiteLLM client"
    },
    {
      "wave": 6,
      "tasks": [22, 23],
      "description": "Post-processing + gateway endpoints (core gateway complete)"
    },
    {
      "wave": 7,
      "tasks": [24, 25, 26, 27, 28, 29, 30, 31],
      "description": "Billing, alerts, email, guardrail admin (parallel streams)"
    },
    {
      "wave": 8,
      "tasks": [32, 33, 34, 35, 36, 37],
      "description": "Analytics, dashboards, onboarding"
    },
    {
      "wave": 9,
      "tasks": [38, 39, 40, 41, 42, 43],
      "description": "Security hardening, cache wiring, CSV export"
    },
    {
      "wave": 10,
      "tasks": [44, 45],
      "description": "E2E integration tests + documentation"
    }
  ]
}
```

## Tasks

## Milestone 1: Project Foundation & Infrastructure

- [x] 1. Initialize project structure and Docker Compose setup
  - Create monorepo layout: `backend/`, `frontend/`, `litellm/`, `infra/`
  - Write `docker-compose.yml` with services: `api`, `litellm`, `postgres`, `redis`, `dashboard`
  - Configure internal Docker network so `litellm` is NOT exposed to internet
  - Create `.env.example` with all required environment variables
  - Add `Makefile` with commands: `make dev`, `make migrate`, `make seed`
  - **Requirement:** 15 (AC1, AC2, AC3)

- [x] 2. Initialize FastAPI backend skeleton
  - Create `backend/` Python project with `pyproject.toml` (FastAPI, SQLAlchemy, asyncpg, httpx, redis, python-jose, bcrypt, stripe, resend)
  - Set up application factory pattern in `backend/app/main.py`
  - Configure structured JSON logging (requirement: AC6 of Req 15)
  - Add `GET /health` endpoint returning status of PostgreSQL, Redis, and LiteLLM Proxy
  - Add global exception handlers returning consistent JSON error format
  - **Requirement:** 15 (AC5, AC6, AC7)

- [x] 3. Set up PostgreSQL with Alembic migrations
  - Configure SQLAlchemy async engine with connection pool
  - Create Alembic migration environment
  - Write initial migration creating all tables: `users`, `virtual_keys`, `credit_accounts`, `credit_transactions`, `providers`, `models`, `markup_config`, `usage_records`, `provider_balance_log`, `guardrail_keywords`, `guardrail_events`
  - Create seed script: insert default providers (Groq, DeepSeek, Gemini), default models with pricing, global markup_config row (20%)
  - **Requirement:** 15 (AC2); Data Models from design

- [x] 4. Configure LiteLLM Proxy
  - Write `litellm/litellm_config.yaml` with Groq, DeepSeek, Gemini Flash models
  - Configure fallback chains: llama-3.1-8b → deepseek-chat → gemini-flash
  - Configure Redis exact cache (TTL 3600s)
  - Set router timeout to 25s, num_retries=2
  - Verify LiteLLM starts and responds on internal network only
  - **Requirement:** 6 (AC1, AC4), 7 (AC1, AC4)

- [x] 5. Initialize Next.js frontend skeleton
  - Create `frontend/` with Next.js 14 App Router, TypeScript, Tailwind CSS, shadcn/ui
  - Set up route groups: `(marketing)`, `(auth)`, `dashboard`, `admin`
  - Create `lib/api.ts` fetch wrapper with JWT auth header injection
  - Create `lib/auth.ts` for JWT storage and refresh logic
  - Add layout shells for Customer Dashboard and Admin Dashboard with navigation
  - **Requirement:** 10, 11 (structure only)


## Milestone 2: Authentication & User Management

- [x] 6. Implement user registration and login (email/password)
  - `POST /auth/register` — validate email, hash password with bcrypt (cost=12), create user + credit_account (balance=$0), return JWT pair
  - `POST /auth/login` — verify password, check account lock, return access token (15min) + refresh token (7 days)
  - `POST /auth/logout` — invalidate refresh token
  - `GET /auth/me` — return current user info
  - Implement login brute-force protection: track `failed_login_attempts` in DB, lock account for 15 min after 10 failures (Redis counter)
  - **Requirement:** 9 (AC1), 14 (AC2, AC5)

- [x] 7. Implement Google OAuth login
  - `POST /auth/oauth/google` — verify Google ID token, upsert user with `oauth_provider='google'`, return JWT pair
  - Handle case where email already exists with password auth (link accounts)
  - **Requirement:** 9 (AC1)

- [x] 8. Implement JWT middleware and role-based access control
  - Create `middleware/auth.py` — extract and verify JWT from `Authorization: Bearer` header
  - Attach `current_user` to request state
  - Create `require_admin` dependency that rejects non-admin users with HTTP 403
  - Protect all `/api/*` routes with JWT middleware
  - Protect all `/admin/*` routes with `require_admin` dependency
  - **Requirement:** 14 (AC1), 11 (AC9)

- [x] 9. Build auth UI pages (login, register)
  - `/login` page — email/password form + Google OAuth button
  - `/register` page — email/password form with validation
  - Handle JWT storage in httpOnly cookie or localStorage
  - Redirect to `/dashboard` after successful auth
  - Show error messages for invalid credentials
  - **Requirement:** 9 (AC1, AC5)


## Milestone 3: Virtual Key Management

- [x] 10. Implement Virtual Key backend (CRUD + hashing)
  - `POST /api/keys` — generate cryptographically random key (`ag-sk-{32 random chars}`), store SHA-256 hash + prefix, return plaintext once only; enforce max 10 keys per user
  - `GET /api/keys` — list keys with prefix, name, status, last_used_at, total_requests
  - `DELETE /api/keys/{id}` — set `is_active=false`, `revoked_at=now()`, invalidate Redis cache `vk:{key_hash}` immediately
  - Auto-create default key on user registration (name: "Default Key")
  - **Requirement:** 2 (AC1, AC2, AC3, AC4, AC5, AC6, AC7)

- [x] 11. Implement Virtual Key auth middleware for gateway
  - Extract Bearer token from `Authorization` header on all `/v1/*` requests
  - Compute SHA-256 hash, lookup in Redis cache (`vk:{hash}`, TTL 30s), fallback to PostgreSQL
  - Return HTTP 401 if key not found or `is_active=false`
  - Update `last_used_at` asynchronously (background task, not blocking)
  - Log suspicious usage: >10 unique IPs per key per hour → write to security log
  - **Requirement:** 2 (AC4, AC6), 13 (AC4), 14 (AC3, AC7)

- [x] 12. Build API Keys UI page
  - `/dashboard/keys` — table of keys with prefix, name, status badge, created date, usage count
  - "Create Key" modal — name + description + optional RPM limit fields; show full key value once in a copy-to-clipboard dialog
  - "Revoke" button with confirmation dialog
  - **Requirement:** 2 (AC3, AC5), 10 (AC5, AC6)


## Milestone 4: Markup Engine & Provider Management

- [x] 13. Implement markup resolution service
  - `services/markup.py` — `resolve_markup_rate(model_id, provider_id) -> float`
  - Priority: model.markup_rate (if not null) → markup_config[provider] → markup_config[global]
  - Cache resolved rate in Redis: `markup:{model_id}` TTL 60s
  - Invalidate cache on any markup update
  - **Requirement:** 4 (AC1, AC2, AC3, AC4, AC5)

- [x] 14. Implement admin markup configuration endpoints
  - `PUT /admin/markup/global` — update global default markup_rate (0.0–5.0)
  - `PUT /admin/providers/{id}/markup` — set provider-level markup in markup_config
  - `PUT /admin/models/{id}/markup` — set model.markup_rate directly (null = inherit)
  - `GET /admin/models` — return models with resolved markup rate, source level (model/provider/global), base price, and effective sell price
  - **Requirement:** 4 (AC1, AC5, AC6), 11 (AC7)

- [x] 15. Implement provider management endpoints
  - `GET /admin/providers` — list providers with balance, status, thresholds, burn rate
  - `PUT /admin/providers/{id}/balance` — manually update balance, write to provider_balance_log
  - `PUT /admin/providers/{id}/thresholds` — update warning_threshold and hard_stop_threshold (min $1 each)
  - `POST /admin/providers/{id}/release-hard-stop` — set status='normal', clear hard_stop_activated_at, invalidate Redis cache
  - Compute burn rate: sum of base_cost_usd from usage_records in last 24h / 24 = $/hour
  - **Requirement:** 5 (AC1, AC3, AC8, AC9, AC10), 11 (AC5, AC6)

- [x] 16. Build Admin Models & Providers UI pages
  - `/admin/models` — table with model name, provider, base price (input/output per 1M), markup source badge, effective sell price; inline edit for markup rate
  - `/admin/providers` — cards per provider showing balance, status badge (color-coded), warning/hard_stop thresholds, burn rate, days remaining; "Update Balance" form; "Release Hard Stop" button (only visible when status=hard_stop)
  - **Requirement:** 4 (AC6), 5 (AC9), 11 (AC5, AC6, AC7)


## Milestone 5: Core Gateway Pipeline

- [x] 17. Implement credit pre-check and hold service
  - `services/credit.py` — `estimate_max_cost(model_id, max_tokens, markup_rate) -> Decimal`
  - `hold_credit(user_id, request_id, amount)` — `SELECT ... FOR UPDATE` on credit_accounts, check balance ≥ amount, deduct hold amount atomically; return HTTP 402 if insufficient
  - `settle_credit(user_id, request_id, actual_cost)` — write final deduction to credit_accounts, insert credit_transactions record (type='usage'), release hold
  - `release_hold(user_id, request_id)` — restore held amount on error/timeout
  - **Requirement:** 3 (AC2, AC3, AC4, AC5)

- [x] 18. Implement guardrail service
  - `services/guardrail.py` — load banned keywords from DB, cache in Redis (`guardrails:keywords`, TTL 300s, invalidated on update)
  - `check_input(text: str) -> GuardrailResult` — scan all messages in request body
  - `check_output(text: str) -> GuardrailResult` — scan response content
  - Return matched keyword and truncated snippet (first 100 chars) for logging
  - Write `guardrail_events` record on violation
  - **Requirement:** 8 (AC1, AC2, AC3, AC4, AC6)

- [x] 19. Implement rate limiting middleware
  - `middleware/rate_limit.py` — sliding window per Virtual Key using Redis
  - Key pattern: `rate_limit:{virtual_key_id}:{minute_window}`, TTL 120s
  - Skip check if `rate_limit_rpm` is null (unlimited)
  - Return HTTP 429 with `Retry-After` header on limit exceeded
  - **Requirement:** 8 (AC7),(AC3)                                                                                           

- [x] 20. Implement provider balance check and Hard Stop enforcement
  - `services/provider_balance.py` — `check_provider_status(provider_id) -> ProviderStatus`
  - Cache provider status in Redis: `provider_status:{provider_id}` TTL 30s
  - If status='hard_stop': check fallback_provider_id; if exists, rewrite request to fallback; if not, return HTTP 503
  - `deduct_provider_balance(provider_id, amount, request_id)` — UPDATE providers.balance_usd, insert provider_balance_log record
  - `check_thresholds(provider)` — after deduction, compare balance to warning_threshold and hard_stop_threshold; trigger alerts as needed
  - **Requirement:** 5 (AC2, AC4, AC5, AC6, AC7)

- [x] 21. Implement LiteLLM Proxy HTTP client
  - `services/litellm_client.py` — async httpx client with base_url from env `LITELLM_URL`
  - Add `Authorization: Bearer {LITELLM_MASTER_KEY}` header to all requests
  - `post_chat(body: dict) -> dict` — non-streaming call with 30s timeout; raise `LiteLLMTimeoutError` on timeout
  - `stream_chat(body: dict) -> AsyncIterator[bytes]` — streaming call, yield raw SSE bytes
  - `get_models() -> list` — fetch available models from LiteLLM
  - **Requirement:** 1 (AC1, AC2, AC4, AC6)

- [x] 22. Implement post-processing background task
  - `services/post_process.py` — `post_process_usage(request_id, virtual_key_id, user_id, model_id, provider_id, litellm_response, markup_rate, start_time)`
  - Extract actual token counts from LiteLLM response
  - Compute base_cost_usd and billed_amount_usd
  - Call `settle_credit()` with actual billed amount
  - Call `deduct_provider_balance()` with base_cost_usd
  - Insert `usage_records` row with all fields
  - Update `virtual_keys.total_requests`, `total_tokens`, `last_used_at`
  - Call `check_thresholds()` for provider alerts
  - **Requirement:** 1 (AC3), 3 (AC4, AC5), 5 (AC2), 12 (AC1)

- [x] 23. Implement gateway endpoints (OpenAI-compatible)
  - `POST /v1/chat/completions` — wire up full pipeline: auth middleware → credit hold → guardrail input → provider check → LiteLLM call → guardrail output (non-stream) → return response; background task for post-processing
  - `POST /v1/embeddings` — same pipeline minus guardrail output check
  - `GET /v1/models` — proxy to LiteLLM `/v1/models`, filter to active models only
  - Handle `stream: true` — `StreamingResponse` proxying SSE chunks; post-process after stream ends
  - Return OpenAI-compatible error format for all error cases (401, 402, 400, 404, 503, 504)
  - **Requirement:** 1 (AC1, AC2, AC3, AC5, AC6), 13 (AC1, AC2, AC3, AC5, AC6, AC7)


## Milestone 6: Billing & Stripe Integration

- [x] 24. Implement Stripe Checkout top-up flow
  - `POST /api/billing/topup` — create Stripe Checkout Session (mode=payment, min $5), return `checkout_url`
  - Store `stripe_payment_intent_id` in credit_transactions with status='pending'
  - `GET /api/billing/balance` — return current credit_accounts.balance_usd
  - `GET /api/billing/transactions` — paginated list of credit_transactions
  - **Requirement:** 3 (AC6, AC10)

- [x] 25. Implement Stripe Webhook handler
  - `POST /api/billing/webhook` — verify `Stripe-Signature` header using `STRIPE_WEBHOOK_SECRET`; reject with 400 if invalid signature
  - Handle `payment_intent.succeeded` — find pending transaction by payment_intent_id, add amount to credit_accounts.balance_usd, update transaction status='completed', update last_topup_amount + last_topup_at
  - Handle `payment_intent.payment_failed` — update transaction status='failed', log error
  - Send payment confirmation email to Customer on success
  - **Requirement:** 3 (AC7, AC8), 14 (AC6)

- [x] 26. Implement low-balance email alert for customers
  - After each credit settlement in post_process_usage: check if balance < 20% of last_topup_amount
  - If yes and `low_balance_alert_sent_at` is null or > 24h ago: send email via Resend, update `low_balance_alert_sent_at`
  - **Requirement:** 3 (AC9)

- [x] 27. Build Billing UI page
  - `/dashboard/billing` — show current balance prominently, "Add Credits" button (opens amount selector → redirects to Stripe Checkout)
  - Transaction history table: date, type, amount, status badge
  - Handle Stripe redirect back (success/cancel URL params)
  - **Requirement:** 10 (AC7, AC8)


## Milestone 7: Provider Alerts & Email Notifications

- [x] 28. Implement provider balance alert system
  - In `check_thresholds()`: after each balance deduction, compare to warning_threshold and hard_stop_threshold
  - Warning alert: if balance < warning_threshold AND (last_warning_alert_at is null OR > 1 hour ago) → send email to Admin, update last_warning_alert_at
  - Hard Stop activation: if balance < hard_stop_threshold → UPDATE providers SET status='hard_stop', hard_stop_activated_at=now(); DEL Redis cache; send immediate email to Admin
  - **Requirement:** 5 (AC4, AC5, AC8)

- [x] 29. Implement Resend email service
  - `services/email.py` — async Resend client wrapper
  - Templates (plain HTML): welcome, low_credit_warning, payment_confirmation, provider_warning, provider_hard_stop
  - `send_welcome(user_email, virtual_key_prefix)` — include Quick Start code snippets
  - `send_low_credit(user_email, balance, topup_url)`
  - `send_payment_confirmation(user_email, amount, new_balance)`
  - `send_provider_warning(admin_email, provider_name, balance, threshold)`
  - `send_provider_hard_stop(admin_email, provider_name, balance)`
  - **Requirement:** 9 (AC6), 3 (AC9), 5 (AC4, AC5, AC8)


## Milestone 8: Guardrails Admin

- [x] 30. Implement guardrail keyword management endpoints
  - `GET /admin/guardrails` — list all keywords with scope and status
  - `POST /admin/guardrails` — add keyword (validate non-empty, scope in ['input','output','both'])
  - `PUT /admin/guardrails/{id}` — update keyword or scope
  - `DELETE /admin/guardrails/{id}` — soft delete (is_active=false)
  - On any write: invalidate Redis cache `guardrails:keywords`
  - **Requirement:** 8 (AC5)

- [x] 31. Build Guardrails UI page
  - `/admin/guardrails` — table of keywords with scope badge and active status
  - "Add Keyword" form with keyword text + scope selector
  - Delete button with confirmation
  - Show count of guardrail events triggered in last 7 days
  - **Requirement:** 8 (AC5), 11 (AC8)


## Milestone 9: Usage Analytics & Customer Dashboard

- [x] 32. Implement usage query endpoints
  - `GET /api/usage` — paginated usage_records for current user; filter by virtual_key_id, model_name, date range; return fields: timestamp, model, provider, tokens, billed_amount, latency_ms, cache_hit, is_fallback
  - `GET /api/usage/summary` — aggregate stats: total_requests, total_tokens, total_cost, cache_hit_rate, error_rate grouped by day for last 30 days
  - `GET /admin/usage/export` — stream CSV of usage_records for date range (admin only)
  - **Requirement:** 12 (AC1, AC3, AC4, AC6), 10 (AC3, AC4)

- [x] 33. Build Customer Dashboard Overview and Usage pages
  - `/dashboard` (Overview) — credit balance card (auto-refresh every 60s), usage summary for today (requests, tokens, cost), recent 5 API calls table
  - `/dashboard/usage` — daily usage bar chart (Recharts) for last 30 days; filterable table of usage records with Virtual Key, model, tokens, cost, latency, cache hit badge
  - **Requirement:** 10 (AC2, AC3, AC4)

- [x] 34. Implement admin analytics endpoints
  - `GET /admin/overview` — total customers, today's revenue, today's requests, provider status summary
  - `GET /admin/customers` — list users with credit_balance, total_requests (last 30d), total_spend (last 30d), account status
  - `GET /admin/customers/{id}` — customer detail with usage_records (paginated)
  - **Requirement:** 11 (AC2, AC3, AC4), 12 (AC3)

- [x] 35. Build Admin Dashboard Overview and Customers pages
  - `/admin` (Overview) — KPI cards: total customers, today revenue, today requests; provider status row (color-coded badges); simple line chart of daily revenue last 30 days
  - `/admin/customers` — searchable table with credit balance, usage, status; click row to see customer detail with usage history
  - **Requirement:** 11 (AC2, AC3, AC4)


## Milestone 10: Onboarding & Quick Start

- [x] 36. Implement new user onboarding flow
  - On successful registration: create credit_account with $1.00 free credit, insert credit_transactions record (type='free_credit', amount=1.00), auto-create default Virtual Key named "Default Key"
  - Trigger welcome email (via Resend) within 2 minutes of registration
  - Return Virtual Key plaintext in registration response (shown once)
  - **Requirement:** 9 (AC2, AC4, AC6)

- [x] 37. Build Quick Start page
  - `/dashboard/quickstart` — shown automatically after first login if no API calls made yet
  - Display Virtual Key with copy button (masked after first view)
  - Code snippets in tabs: Python (openai SDK), Node.js (openai SDK), cURL — all pre-filled with user's actual Virtual Key and a working model name
  - "Make your first call" section with a live test button that calls `/v1/chat/completions` and shows the response inline
  - **Requirement:** 9 (AC3, AC5)


## Milestone 11: Security Hardening & Production Readiness

- [x] 38. Implement Provider API key encryption at rest
  - Enable `pgcrypto` extension in PostgreSQL
  - Add `encrypted_api_key` column to providers table (or a separate `provider_credentials` table)
  - Encrypt/decrypt using AES-256 with `DB_ENCRYPTION_KEY` from environment
  - LiteLLM Proxy reads keys from environment variables (not from AstraGate DB) — document this separation clearly
  - **Requirement:** 14 (AC4)

- [x] 39. Add HTTPS and security headers
  - Add Caddy or Nginx reverse proxy service to docker-compose for TLS termination
  - Configure security headers: `Strict-Transport-Security`, `X-Content-Type-Options`, `X-Frame-Options`
  - Ensure LiteLLM container has no published ports (internal network only)
  - **Requirement:** 14 (AC1), 1 (AC4)

- [x] 40. Add structured logging and observability
  - Ensure all request logs include: request_id, virtual_key_prefix, model, latency_ms, status_code
  - Log all guardrail events, Hard Stop activations, and failed Stripe webhooks
  - Add `GET /health` detailed response: `{"status": "ok", "postgres": "ok", "redis": "ok", "litellm": "ok"}`
  - Verify 503 response and auto-reconnect on DB connection loss
  - **Requirement:** 12 (AC2, AC5), 15 (AC5, AC6, AC7)

- [x] 41. Performance validation
  - Write a simple load test script (locust or k6) simulating 100 concurrent requests to `/v1/chat/completions` against a mock LiteLLM
  - Verify p95 latency overhead from AstraGate middleware (auth + credit check + guardrail) is under 50ms
  - Verify no credit race conditions under concurrent load (run 50 simultaneous requests from same user)
  - **Requirement:** 15 (AC4)


## Milestone 12: End-to-End Integration & Polish

- [x] 42. Wire up cache hit detection and zero-cost billing
  - Detect `cache_hit` from LiteLLM response metadata
  - When `cache_hit=true`: skip credit deduction (billed_amount=0), skip provider balance deduction, still write usage_record with `cache_hit=true`
  - Add cache hit rate to usage summary endpoints and dashboard analytics
  - Add Admin toggle for Exact Cache (update litellm_config.yaml cache section, restart LiteLLM container)
  - **Requirement:** 7 (AC2, AC3, AC5, AC6)

- [x] 43. Implement CSV export for usage records
  - `GET /admin/usage/export?from=YYYY-MM-DD&to=YYYY-MM-DD` — stream CSV with headers: timestamp, customer_email, virtual_key_prefix, model, provider, input_tokens, output_tokens, base_cost_usd, markup_rate, billed_amount_usd, latency_ms, cache_hit
  - Use streaming response to avoid memory issues for large exports
  - **Requirement:** 12 (AC6)

- [x] 44. Final integration testing and smoke tests
  - End-to-end test: register → get Virtual Key → call `/v1/chat/completions` → verify usage_record created, credit deducted, provider balance deducted
  - Test credit insufficient flow: set balance to $0.001, verify HTTP 402 returned
  - Test Hard Stop flow: set provider balance below hard_stop_threshold, verify requests blocked and fallback used
  - Test guardrail: add banned keyword, send prompt containing it, verify HTTP 400 returned
  - Test Stripe webhook: send mock `payment_intent.succeeded` event, verify credit added
  - Test streaming: verify SSE chunks arrive incrementally, not buffered
  - **Requirement:** All

- [x] 45. Documentation and deployment guide
  - Write `README.md` with: architecture overview, local dev setup (`make dev`), environment variables reference, how to add a new Provider to LiteLLM config
  - Write `DEPLOYMENT.md` with: production checklist (TLS, secrets, DB backups), how to update Provider balances, how to release a Hard Stop
  - Add API reference page in dashboard (`/docs`) linking to OpenAPI spec at `/openapi.json`
  - **Requirement:** 9 (AC3)


## Notes

- **Phase 1 only:** Tasks 1–45 cover MVP scope. Phase 2 items (team accounts, semantic cache, postpaid billing, AI guardrails) are explicitly out of scope.
- **LiteLLM key separation:** LiteLLM Proxy holds all Provider API keys via environment variables. AstraGate never reads or stores Provider keys directly. This is enforced at the Docker network level.
- **Credit race conditions:** Task 17 uses `SELECT ... FOR UPDATE` on credit_accounts to prevent concurrent requests from overdrawing. Redis hold keys provide a secondary safety net.
- **Streaming guardrails:** Output guardrail is intentionally skipped for streaming responses (Phase 1 trade-off). Only input guardrail applies to streaming requests.
- **Provider balance is manual:** No automatic sync with Provider APIs. Admin must update balances manually after topping up. The system tracks estimated spend, not actual Provider-reported spend.
- **Email rate limiting:** Provider warning emails are throttled to 1/hour per provider (tracked via `last_warning_alert_at`). Hard Stop emails are always sent immediately.
- **Free credit:** $1 free credit on signup is granted as a `free_credit` transaction type. No payment method required.
- **Deployment order:** Run migrations (`make migrate`) before starting the API. Seed data (`make seed`) must run before any gateway requests (requires providers + models + global markup to exist).
