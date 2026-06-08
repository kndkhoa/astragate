# AstraGate — Deployment Guide

## Production Checklist

### 1. Security

- [ ] Generate strong secrets for all `*_SECRET` and `*_KEY` environment variables:
  ```bash
  openssl rand -hex 32  # For JWT_SECRET, JWT_REFRESH_SECRET, DB_ENCRYPTION_KEY, LITELLM_MASTER_KEY
  ```
- [ ] Never commit `.env` to version control
- [ ] Ensure LiteLLM container has **no published ports** (internal network only)
- [ ] Set `APP_ENV=production` to disable debug features
- [ ] Configure a reverse proxy (Caddy/Nginx/Traefik) for TLS termination
- [ ] Set `NEXT_PUBLIC_API_URL` to your production HTTPS domain
- [ ] Verify CORS settings if frontend and API are on different domains

### 2. TLS Configuration

AstraGate ships with security headers in the FastAPI middleware:
- `Strict-Transport-Security: max-age=31536000; includeSubDomains`
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `X-XSS-Protection: 1; mode=block`

For TLS termination, add a reverse proxy. Example with Caddy:

```
# Caddyfile
api.yourdomain.com {
    reverse_proxy api:8000
}

dashboard.yourdomain.com {
    reverse_proxy dashboard:3000
}
```

### 3. Database

- [ ] Use a managed PostgreSQL 15 instance (AWS RDS, Cloud SQL, etc.)
- [ ] Enable automated daily backups with 7-day retention
- [ ] Set strong `POSTGRES_PASSWORD`
- [ ] Restrict network access to only the AstraGate API service
- [ ] Run migrations before deploying: `alembic upgrade head`
- [ ] Encryption at rest: Provider API keys are encrypted with AES-256 using `DB_ENCRYPTION_KEY`

### 4. Redis

- [ ] Use a managed Redis 7 instance (ElastiCache, Memorystore, etc.)
- [ ] Enable persistence (AOF or RDB snapshots)
- [ ] Restrict network access to only the AstraGate API service
- [ ] Consider Redis Sentinel or Cluster for high availability

### 5. Stripe

- [ ] Switch from `sk_test_*` to `sk_live_*` keys
- [ ] Configure webhook endpoint in Stripe Dashboard: `https://api.yourdomain.com/api/billing/webhook`
- [ ] Set `STRIPE_WEBHOOK_SECRET` from the Stripe webhook configuration
- [ ] Test with Stripe CLI: `stripe trigger payment_intent.succeeded`

### 6. Email (Resend)

- [ ] Configure a verified sending domain in Resend
- [ ] Set `RESEND_API_KEY` with production key
- [ ] Update email templates in `app/services/email.py` to use your domain URLs

---

## Deployment Steps

### Option A: Docker Compose (single server)

```bash
# On your production server
git pull origin main
cp .env.production .env

# Start services
docker compose -f docker-compose.yml up -d --build

# Run migrations
docker compose exec api alembic upgrade head

# Seed initial data (first deployment only)
docker compose exec api python -m app.scripts.seed

# Verify health
curl https://api.yourdomain.com/health
```

### Option B: Kubernetes (scaling)

Deploy each service as a separate Deployment:
- `api`: 2+ replicas, horizontal pod autoscaler
- `dashboard`: 2+ replicas (stateless)
- `litellm`: 1 replica (internal ClusterIP only, no Ingress)
- `postgres`: Managed service (external)
- `redis`: Managed service (external)

---

## Operational Procedures

### Updating Provider Balances

When you top up credit with an upstream provider (Groq, DeepSeek, Gemini), update the balance in AstraGate so it accurately tracks spend:

```bash
# Via Admin API
curl -X PUT https://api.yourdomain.com/admin/providers/{provider_id}/balance \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{"balance_usd": 100.00, "note": "Topped up $100 on 2025-01-15"}'
```

Or via the Admin Dashboard: **Admin → Providers → [Provider Card] → Update Balance**.

### Releasing a Hard Stop

When a provider hits its hard-stop threshold, all requests to that provider are blocked (or routed to fallback). To resume:

1. Top up your account with the upstream provider
2. Update the balance in AstraGate (see above)
3. Release the hard stop:

```bash
curl -X POST https://api.yourdomain.com/admin/providers/{provider_id}/release-hard-stop \
  -H "Authorization: Bearer <admin_token>"
```

Note: The release will fail if the current balance is still below `hard_stop_threshold`. Update the balance first.

### Monitoring Alerts

AstraGate sends email alerts for:
- **Provider Warning** — balance below `warning_threshold` (max 1 email/hour per provider)
- **Provider Hard Stop** — balance below `hard_stop_threshold` (immediate, always sent)
- **Customer Low Balance** — balance below 20% of last top-up (max 1 email/24h per customer)

Configure thresholds per provider:
```bash
curl -X PUT https://api.yourdomain.com/admin/providers/{provider_id}/thresholds \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{"warning_threshold": 20.00, "hard_stop_threshold": 5.00}'
```

### Adding a New Provider

1. Sign up and get API key from the provider
2. Add to `.env`:
   ```
   NEWPROVIDER_API_KEY=sk-xxxxx
   ```
3. Add to `docker-compose.yml` environment for `litellm` service
4. Edit `litellm/litellm_config.yaml`:
   ```yaml
   model_list:
     - model_name: newprovider-model
       litellm_params:
         model: newprovider/model-name
         api_key: os.environ/NEWPROVIDER_API_KEY
   ```
5. Restart LiteLLM: `docker compose restart litellm`
6. Add provider + model records to database via admin API or seed script

### Viewing Logs

```bash
# All API logs (structured JSON)
docker compose logs -f api

# Filter for errors only
docker compose logs -f api | grep '"level":"error"'

# Filter for a specific request
docker compose logs -f api | grep "request_id=<id>"
```

### Backup & Restore

```bash
# Backup PostgreSQL
docker compose exec postgres pg_dump -U astragate astragate > backup_$(date +%Y%m%d).sql

# Restore
cat backup_20250115.sql | docker compose exec -T postgres psql -U astragate astragate
```

---

## Scaling Considerations

| Component | Scaling Strategy |
|-----------|-----------------|
| API | Horizontal (multiple replicas behind load balancer) |
| Dashboard | Horizontal (stateless Next.js) |
| PostgreSQL | Vertical first, then read replicas |
| Redis | Vertical first, then Redis Cluster |
| LiteLLM | Single instance (stateless, low resource) |

### Performance Benchmarks

With the k6 load test (`backend/tests/load_test.js`):
- 100 concurrent users sustained for 30s
- Middleware overhead (auth + credit check + guardrail) target: p95 < 50ms
- Credit race condition safety verified under 50 simultaneous requests

Run the load test:
```bash
k6 run backend/tests/load_test.js
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `/health` returns 503 | DB or Redis unreachable | Check connection URLs, network |
| 402 on all requests | Customer balance is $0 | Top up via Stripe or admin |
| 503 on requests | Provider in hard_stop, no fallback | Release hard stop or add fallback |
| Emails not sending | `RESEND_API_KEY` not set or invalid | Verify key, check mock mode logs |
| Stripe webhooks failing | Wrong `STRIPE_WEBHOOK_SECRET` | Re-copy from Stripe Dashboard |
| LiteLLM timeout (504) | Provider slow or down | Check provider status, increase timeout |
