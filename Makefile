.PHONY: dev migrate seed down logs ps build

# ---------------------------------------------------------------------------
# Local development
# ---------------------------------------------------------------------------

## Start all services in the background
dev:
	docker compose up --build -d

## Stop all services and remove containers (volumes are preserved)
down:
	docker compose down

## Rebuild images without cache
build:
	docker compose build --no-cache

## Tail logs from all services (Ctrl+C to stop)
logs:
	docker compose logs -f

## Show running containers
ps:
	docker compose ps

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

## Run Alembic migrations inside the api container
migrate:
	docker compose exec api alembic upgrade head

## Run the seed script inside the api container
seed:
	docker compose exec api python -m app.scripts.seed

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

## Open a psql shell inside the postgres container
psql:
	docker compose exec postgres psql -U astragate -d astragate

## Open a Redis CLI shell inside the redis container
redis-cli:
	docker compose exec redis redis-cli
