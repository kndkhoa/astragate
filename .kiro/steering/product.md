# AstraGate — Product Overview

AstraGate is a self-hosted LLM API gateway that provides a single OpenAI-compatible endpoint in front of multiple LLM providers (Groq, DeepSeek, Gemini, etc.). It is a B2C SaaS product where customers prepay with credits and consume them per request.

## Core Capabilities

- **Unified API**: OpenAI-compatible `/v1/chat/completions`, `/v1/embeddings`, and `/v1/models` endpoints that proxy to any configured provider via LiteLLM.
- **Virtual Keys**: Customers create scoped API keys with optional per-key rate limits (RPM). Keys are hashed at rest; only the prefix is stored in plaintext.
- **Prepaid Credits**: Customers top up via Stripe Checkout. Each request deducts `base_cost × (1 + markup_rate)` from their credit balance. Requests are blocked when balance is insufficient.
- **Provider Management**: Admins configure providers with prepaid balances, warning thresholds, and hard-stop thresholds. Automatic fallback to a secondary provider when a primary hits its hard-stop.
- **Guardrails**: Keyword-based content filtering on request prompts and/or LLM responses. Violations are logged as guardrail events and the request is blocked.
- **Admin Dashboard**: Manage providers, models, markup rates, guardrail keywords, and view customer accounts.
- **Customer Dashboard**: View credit balance, usage history, manage virtual keys, and top up.

## User Roles

- `admin` — full platform access including provider/model/guardrail management
- `customer` — self-service access to their own keys, usage, and billing

## Key Business Rules

- LiteLLM is **never** exposed to the internet; it is only reachable on the internal Docker network.
- Credit deduction happens atomically after a successful LLM response.
- Provider balances are tracked separately from customer credits (provider balance = what AstraGate has prepaid with the upstream provider).
