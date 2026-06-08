"""Quick E2E demo against the running AstraGate API on localhost:8000."""
import time
import uuid
import httpx

BASE = "http://localhost:8000"
email = f"demo-{uuid.uuid4().hex[:8]}@test.com"

with httpx.Client(timeout=40.0) as c:
    # 1. Register
    r = c.post(f"{BASE}/auth/register", json={"email": email, "password": "password123"})
    print("REGISTER:", r.status_code)
    data = r.json()
    token = data["access_token"]
    key = data["default_key"]
    print("  email:", email)
    print("  default key:", key)

    # 2. Balance
    r = c.get(f"{BASE}/api/billing/balance", headers={"Authorization": f"Bearer {token}"})
    print("BALANCE:", r.json())

    # 3. Chat completion through the gateway
    r = c.post(
        f"{BASE}/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": "gemini-flash",
            "messages": [{"role": "user", "content": "What is 2+2? One word."}],
            "max_tokens": 20,
        },
    )
    print("CHAT:", r.status_code)
    if r.status_code == 200:
        body = r.json()
        print("  response:", body["choices"][0]["message"]["content"])
        print("  tokens:", body["usage"]["total_tokens"])
    else:
        print("  error:", r.text)

    # 4. Wait for background post-processing, then check usage + balance
    time.sleep(2)
    r = c.get(f"{BASE}/api/usage", headers={"Authorization": f"Bearer {token}"})
    usage = r.json()
    print("USAGE records:", usage["pagination"]["total_count"])
    if usage["records"]:
        rec = usage["records"][0]
        print("  model:", rec["model_name"], "| billed: $", rec["billed_amount_usd"], "| tokens:", rec["total_tokens"])

    r = c.get(f"{BASE}/api/billing/balance", headers={"Authorization": f"Bearer {token}"})
    print("BALANCE after:", r.json())

    # 5. Models list
    r = c.get(f"{BASE}/v1/models", headers={"Authorization": f"Bearer {key}"})
    print("MODELS:", [m["id"] for m in r.json().get("data", [])])
