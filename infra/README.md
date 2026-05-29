# AstraGate — Infrastructure

This directory contains infrastructure-as-code and deployment configuration for AstraGate.

## Phase 1 (Docker Compose)

Phase 1 deployment is handled entirely by `docker-compose.yml` in the repo root.

```bash
make dev      # Start all services locally
make migrate  # Run database migrations
make seed     # Seed initial data
```

## Planned Contents (Phase 2+)

```
infra/
├── caddy/
│   └── Caddyfile          # TLS termination + reverse proxy config
├── kubernetes/
│   ├── namespace.yaml
│   ├── api-deployment.yaml
│   ├── litellm-deployment.yaml
│   ├── postgres-statefulset.yaml
│   └── redis-statefulset.yaml
└── terraform/             # Cloud infrastructure (if needed)
```

## Security Notes

- LiteLLM Proxy has **no published ports** — it is only reachable on the internal Docker network.
- All inter-service communication happens over the `internal` bridge network.
- TLS termination is handled by Caddy/Nginx (to be added in Task 39).
