import http from "k6/http";
import { check, sleep } from "k6";
import { Counter, Rate, Trend } from "k6/metrics";

const gatewayLatency = new Trend("gateway_latency_ms", true);
const successRate = new Rate("success_rate");
const creditErrors = new Counter("credit_402_errors");
const rateLimitErrors = new Counter("rate_limit_429_errors");
const serverErrors = new Counter("server_5xx_errors");

const BASE_URL = __ENV.ASTRAGATE_URL || "http://localhost:8000";
const API_KEY = __ENV.ASTRAGATE_TEST_KEY || "ag-sk-test-key-for-load-testing";

export const options = {
  vus: 500,
  duration: "30s",
  thresholds: {
    success_rate: ["rate>0.95"],
    http_req_failed: ["rate<0.05"],
  },
};

export default function () {
  const payload = JSON.stringify({
    model: "llama-3.1-8b",
    messages: [
      {
        role: "user",
        content: `Hello from k6 load test VU ${__VU} iter ${__ITER}`,
      },
    ],
    max_tokens: 50,
  });

  const params = {
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${API_KEY}`,
    },
    timeout: "30s",
  };

  const res = http.post(`${BASE_URL}/v1/chat/completions`, payload, params);

  gatewayLatency.add(res.timings.duration);

  const is2xx = res.status >= 200 && res.status < 300;
  successRate.add(is2xx);

  if (res.status === 402) {
    creditErrors.add(1);
  } else if (res.status === 429) {
    rateLimitErrors.add(1);
  } else if (res.status >= 500) {
    serverErrors.add(1);
  }

  if (is2xx) {
    check(res, {
      "has choices": (r) => {
        const body = JSON.parse(r.body);
        return body.choices && body.choices.length > 0;
      },
      "has usage": (r) => {
        const body = JSON.parse(r.body);
        return body.usage !== undefined;
      },
    });
  }

  sleep(0.1);
}

export function handleSummary(data) {
  const p95 = data.metrics.gateway_latency_ms
    ? data.metrics.gateway_latency_ms.values["p(95)"]
    : "N/A";
  const p99 = data.metrics.gateway_latency_ms
    ? data.metrics.gateway_latency_ms.values["p(99)"]
    : "N/A";
  const successPct = data.metrics.success_rate
    ? (data.metrics.success_rate.values.rate * 100).toFixed(1)
    : "N/A";

  console.log("\n╔══════════════════════════════════════════════╗");
  console.log("║        AstraGate Load Test (500 VUs)         ║");
  console.log("╠══════════════════════════════════════════════╣");
  console.log(`║  p95 Latency:     ${String(p95).padEnd(10)} ms           ║`);
  console.log(`║  p99 Latency:     ${String(p99).padEnd(10)} ms           ║`);
  console.log(`║  Success Rate:    ${successPct}%                    ║`);
  console.log(`║  Credit Errors:   ${data.metrics.credit_402_errors ? data.metrics.credit_402_errors.values.count : 0}                          ║`);
  console.log(`║  Rate Limits:     ${data.metrics.rate_limit_429_errors ? data.metrics.rate_limit_429_errors.values.count : 0}                          ║`);
  console.log(`║  Server Errors:   ${data.metrics.server_5xx_errors ? data.metrics.server_5xx_errors.values.count : 0}                          ║`);
  console.log("╚══════════════════════════════════════════════╝\n");

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
