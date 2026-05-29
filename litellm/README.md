# LiteLLM Proxy — AstraGate Configuration

LiteLLM Proxy acts as the unified LLM routing layer for AstraGate. It handles provider API calls, fallback routing, and exact caching. AstraGate's FastAPI backend communicates with it over the internal Docker network at `http://litellm:4000`.

---

## How LiteLLM Proxy Is Configured

Configuration lives in `litellm_config.yaml`, which is mounted into the container at `/app/config.yaml`.

The config has four sections:

| Section | Purpose |
|---|---|
| `model_list` | Declares all models and their provider mappings, including fallback entries |
| `router_settings` | Controls retry behavior, timeout, and routing strategy |
| `cache` | Configures Redis-backed exact cache |
| `litellm_settings` | Global LiteLLM behavior flags |

### Current models

| Model name (AstraGate alias) | Provider model | API key env var |
|---|---|---|
| `llama-3.1-8b` | `groq/llama-3.1-8b-instant` | `GROQ_API_KEY` |
| `deepseek-chat` | `deepseek/deepseek-chat` | `DEEPSEEK_API_KEY` |
| `gemini-flash` | `gemini/gemini-1.5-flash` | `GEMINI_API_KEY` |

---

## Fallback Chain Logic

LiteLLM supports multiple entries for the same `model_name`. When the primary entry fails (timeout, rate limit, provider error), LiteLLM automatically tries the next entry with the same name.

Current fallback chains:

```
llama-3.1-8b  →  deepseek-chat (DeepSeek)  →  (no further fallback)
deepseek-chat →  gemini-flash (Gemini)      →  (no further fallback)
gemini-flash  →  (no fallback configured)
```

This is implemented by listing duplicate `model_name` entries in `model_list`:

```yaml
# Primary
- model_name: llama-3.1-8b
  litellm_params:
    model: groq/llama-3.1-8b-instant
    api_key: os.environ/GROQ_API_KEY

# Fallback for llama-3.1-8b
- model_name: llama-3.1-8b
  litellm_params:
    model: deepseek/deepseek-chat
    api_key: os.environ/DEEPSEEK_API_KEY
```

The router retries up to `num_retries: 2` times before giving up, with `retry_after: 0` (no delay between retries).

---

## How to Add a New Provider / Model

1. **Add the model entry** to `litellm_config.yaml` under `model_list`:

   ```yaml
   - model_name: my-new-model
     litellm_params:
       model: openai/gpt-4o-mini        # LiteLLM provider/model string
       api_key: os.environ/OPENAI_API_KEY
   ```

2. **Add the API key** to `.env` and to the `litellm` service's `environment` block in `docker-compose.yml`:

   ```yaml
   litellm:
     environment:
       OPENAI_API_KEY: ${OPENAI_API_KEY}
   ```

3. **Optionally add a fallback** by adding a second entry with the same `model_name` pointing to a different provider.

4. **Restart LiteLLM** to pick up the new config (see below).

5. **Seed the database** — add a row to the `providers` and `models` tables so AstraGate's billing and markup engine knows about the new model.

---

## LiteLLM Is NOT Exposed to the Internet

The `litellm` service in `docker-compose.yml` has **no `ports` mapping**. It is only reachable from other containers on the `internal` Docker bridge network.

```yaml
litellm:
  # NOTE: No "ports:" key here — intentional.
  networks:
    - internal
```

AstraGate's API container reaches it via Docker's internal DNS: `http://litellm:4000`. External traffic cannot reach port 4000 directly.

---

## Exact Cache (Redis)

LiteLLM uses Redis for exact-match caching. Identical prompts (same model + same messages array) return a cached response without hitting the provider.

- **Backend:** Redis at `redis:6379` (internal network)
- **TTL:** 3600 seconds (1 hour)
- **Type:** `exact` — only byte-for-byte identical requests hit the cache

Cache hits are reflected in the `usage_records` table (`cache_hit = true`) and are billed at $0 to the customer.

---

## How to Update the Config and Restart

After editing `litellm_config.yaml`, restart the LiteLLM container to apply changes:

```bash
docker compose restart litellm
```

To verify it started correctly and is healthy:

```bash
docker compose logs litellm --tail=50
```

LiteLLM exposes a health endpoint on the internal network. From the API container you can check:

```bash
curl http://litellm:4000/health
```

Or from the host via the AstraGate health endpoint:

```bash
curl http://localhost:8000/health
```

---

## Router Settings Reference

| Setting | Value | Reason |
|---|---|---|
| `routing_strategy` | `simple-shuffle` | Randomly distributes load across entries with the same model name |
| `num_retries` | `2` | Retry up to 2 times on failure before falling back |
| `timeout` | `25` | AstraGate enforces a 30s gateway timeout; LiteLLM gets 25s of headroom |
| `retry_after` | `0` | No delay between retries (fail fast) |
