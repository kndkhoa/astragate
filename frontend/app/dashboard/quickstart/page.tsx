"use client";

import { useCallback, useEffect, useState } from "react";
import { Copy, Check, Terminal, Play, AlertTriangle, Key } from "lucide-react";
import { get, post } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface VirtualKey {
  id: string;
  name: string;
  key_prefix: string;
  is_active: boolean;
}

interface KeysResponse {
  keys: VirtualKey[];
}

export default function QuickStartPage() {
  const [keys, setKeys] = useState<VirtualKey[]>([]);
  const [selectedKeyPrefix, setSelectedKeyPrefix] = useState("ag-sk-your-key-here");
  const [userKey, setUserKey] = useState("");
  const [activeTab, setActiveTab] = useState<"curl" | "python" | "node">("curl");
  const [copied, setCopied] = useState(false);
  const [loading, setLoading] = useState(true);

  // Live test states
  const [testModel, setTestModel] = useState("llama-3.1-8b");
  const [testPrompt, setTestPrompt] = useState("Explain quantum computing in one sentence.");
  const [testResponse, setTestResponse] = useState("");
  const [testing, setTesting] = useState(false);
  const [testError, setTestError] = useState<string | null>(null);

  useEffect(() => {
    async function fetchKeys() {
      try {
        setLoading(true);
        const data = await get<KeysResponse>("/api/keys");
        const activeKeys = data.keys.filter(k => k.is_active);
        setKeys(activeKeys);
        if (activeKeys.length > 0) {
          setSelectedKeyPrefix(`${activeKeys[0].key_prefix}...`);
        }
      } catch (err) {
        console.error("Failed to load keys", err);
      } finally {
        setLoading(false);
      }
    }
    fetchKeys();
  }, []);

  const getEffectiveKey = () => {
    return userKey.trim() || "ag-sk-your-key-here";
  };

  const getCurlCode = () => {
    return `curl -X POST http://localhost:8000/v1/chat/completions \\
  -H "Authorization: Bearer ${getEffectiveKey()}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "${testModel}",
    "messages": [{"role": "user", "content": "${testPrompt}"}]
  }'`;
  };

  const getPythonCode = () => {
    return `import openai

client = openai.OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="${getEffectiveKey()}"
)

response = client.chat.completions.create(
    model="${testModel}",
    messages=[{"role": "user", "content": "${testPrompt}"}]
)

print(response.choices[0].message.content)`;
  };

  const getNodeCode = () => {
    return `const OpenAI = require('openai');

const openai = new OpenAI({
  baseURL: 'http://localhost:8000/v1',
  apiKey: '${getEffectiveKey()}'
});

async function main() {
  const completion = await openai.chat.completions.create({
    model: '${testModel}',
    messages: [{ role: 'user', content: '${testPrompt}' }],
  });

  console.log(completion.choices[0].message.content);
}

main();`;
  };

  async function handleCopy() {
    const code = activeTab === "curl" ? getCurlCode() : activeTab === "python" ? getPythonCode() : getNodeCode();
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      const textarea = document.createElement("textarea");
      textarea.value = code;
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      document.body.removeChild(textarea);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  }

  async function handleLiveTest() {
    if (!userKey.trim()) {
      setTestError("Please enter your plaintext virtual key to make a live call.");
      return;
    }

    try {
      setTesting(true);
      setTestError(null);
      setTestResponse("");

      // We make a direct fetch to the gateway completion route
      const response = await fetch("http://localhost:8000/v1/chat/completions", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${userKey.trim()}`,
        },
        body: JSON.stringify({
          model: testModel,
          messages: [{ role: "user", content: testPrompt }],
        }),
      });

      const result = await response.json();
      if (!response.ok) {
        throw new Error(result.error?.message || result.detail || "API request failed");
      }

      setTestResponse(result.choices?.[0]?.message?.content || JSON.stringify(result, null, 2));
    } catch (err) {
      setTestError(err instanceof Error ? err.message : "Failed to execute live test call");
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Quick Start Guide</h1>
        <p className="text-sm text-muted-foreground">
          Unified and tracked LLM calls through AstraGate in 2 minutes
        </p>
      </div>

      {/* Model & Key Selection Note */}
      <div className="rounded-xl border border-blue-200/40 bg-blue-50/5 p-5 space-y-3 dark:border-blue-900/40 dark:bg-blue-950/5">
        <h4 className="font-semibold text-sm text-blue-700 dark:text-blue-400">💡 Lựa chọn Model & API Key thống nhất</h4>
        <p className="text-xs leading-relaxed text-muted-foreground">
          Với AstraGate, bạn chỉ cần dùng <strong>1 API Key (Virtual Key) duy nhất</strong> để truy cập vào nhiều mô hình AI khác nhau. 
          Khi gọi API, bạn có thể tùy chọn mô hình muốn sử dụng bằng cách thay đổi giá trị của trường <code>&quot;model&quot;</code> trong request body. 
          AstraGate sẽ tự động kiểm tra số dư ví, định tuyến request và kích hoạt luồng fallback (dự phòng) đến các Provider phù hợp.
        </p>
        <div className="text-xs text-muted-foreground space-y-1.5">
          <span className="font-semibold">Các model khả dụng hiện tại:</span>
          <ul className="list-disc pl-5 space-y-1 font-mono text-[11px]">
            <li><code>llama-3.1-8b</code> (Llama 3.1 8B Instant - Định tuyến qua Groq)</li>
            <li><code>deepseek-chat</code> (DeepSeek Chat - Định tuyến qua DeepSeek)</li>
            <li><code>gemini-flash</code> (Gemini 1.5 Flash - Định tuyến qua Google Gemini)</li>
          </ul>
        </div>
      </div>

      {/* API Key settings */}
      <div className="rounded-xl border bg-card p-6 text-card-foreground shadow-sm space-y-4">
        <div className="flex items-center gap-2">
          <Key className="h-5 w-5 text-muted-foreground" />
          <h3 className="font-semibold text-lg leading-none tracking-tight">API Authentication</h3>
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="registered-keys">Your Active Keys (Prefixes)</Label>
            {loading ? (
              <div className="text-sm text-muted-foreground">Checking active keys...</div>
            ) : keys.length === 0 ? (
              <div className="text-xs text-yellow-600 flex items-center gap-1.5">
                <AlertTriangle className="h-3.5 w-3.5" />
                No active API keys found. Please create one on the API Keys page first.
              </div>
            ) : (
              <div className="text-sm font-semibold font-mono rounded bg-muted px-3 py-2 border">
                {selectedKeyPrefix}
              </div>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="paste-plaintext-key">Paste Plaintext Key *</Label>
            <Input
              id="paste-plaintext-key"
              type="password"
              placeholder="ag-sk-..."
              value={userKey}
              onChange={(e) => {
                setUserKey(e.target.value);
                setTestError(null);
              }}
            />
            <p className="text-xs text-muted-foreground">
              Paste your plaintext key to pre-fill the sample code tabs and run the live test.
            </p>
          </div>
        </div>
      </div>

      {/* Code Snippets section */}
      <div className="rounded-xl border bg-card text-card-foreground shadow-sm overflow-hidden">
        <div className="p-6 border-b flex flex-col md:flex-row md:items-center justify-between gap-4 bg-muted/40">
          <div className="flex items-center gap-2">
            <Terminal className="h-5 w-5 text-muted-foreground" />
            <h3 className="font-semibold text-lg leading-none tracking-tight">Sample Code</h3>
          </div>

          <div className="flex bg-muted rounded-lg p-0.5 border">
            {(["curl", "python", "node"] as const).map((tab) => (
              <Button
                key={tab}
                variant={activeTab === tab ? "default" : "ghost"}
                size="sm"
                className="h-7 text-xs uppercase"
                onClick={() => setActiveTab(tab)}
              >
                {tab}
              </Button>
            ))}
          </div>
        </div>

        {/* Code View */}
        <div className="relative">
          <pre className="p-6 text-sm font-mono bg-muted/20 overflow-x-auto max-h-[300px]">
            <code>
              {activeTab === "curl" ? getCurlCode() : activeTab === "python" ? getPythonCode() : getNodeCode()}
            </code>
          </pre>
          <Button
            size="sm"
            variant="outline"
            className="absolute top-4 right-4 bg-background"
            onClick={handleCopy}
          >
            {copied ? <Check className="h-4 w-4 text-green-600 mr-1.5" /> : <Copy className="h-4 w-4 mr-1.5" />}
            {copied ? "Copied!" : "Copy Code"}
          </Button>
        </div>
      </div>

      {/* Live Test section */}
      <div className="rounded-xl border bg-card p-6 text-card-foreground shadow-sm space-y-6">
        <div className="flex items-center gap-2">
          <Play className="h-5 w-5 text-muted-foreground" />
          <h3 className="font-semibold text-lg leading-none tracking-tight">Make Your First Call</h3>
        </div>

        <div className="grid gap-4 md:grid-cols-3">
          <div className="space-y-4 md:col-span-1 border-r pr-4">
            <div className="space-y-2">
              <Label htmlFor="test-model">Select Model</Label>
              <select
                id="test-model"
                className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                value={testModel}
                onChange={(e) => setTestModel(e.target.value)}
              >
                <option value="llama-3.1-8b">Llama 3.1 8B (Groq)</option>
                <option value="deepseek-chat">DeepSeek Chat (DeepSeek)</option>
                <option value="gemini-flash">Gemini 1.5 Flash (Google)</option>
              </select>
            </div>

            <div className="space-y-2">
              <Label htmlFor="test-prompt">Prompt Message</Label>
              <textarea
                id="test-prompt"
                className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring min-h-[80px]"
                value={testPrompt}
                onChange={(e) => setTestPrompt(e.target.value)}
              />
            </div>

            {testError && (
              <div className="text-sm text-destructive">{testError}</div>
            )}

            <Button
              className="w-full"
              onClick={handleLiveTest}
              disabled={testing || !userKey.trim()}
            >
              <Play className="mr-2 h-4 w-4" />
              {testing ? "Executing..." : "Run Live Test"}
            </Button>
          </div>

          <div className="space-y-2 md:col-span-2 flex flex-col h-full pl-2">
            <Label>Gateway JSON Response</Label>
            <div className="rounded-md border bg-muted/10 p-4 font-mono text-sm overflow-y-auto flex-1 min-h-[200px] max-h-[300px]">
              {testing ? (
                <div className="text-muted-foreground animate-pulse">Waiting for gateway response...</div>
              ) : testResponse ? (
                <div className="whitespace-pre-wrap">{testResponse}</div>
              ) : (
                <div className="text-muted-foreground text-xs">
                  Pasted your plaintext key and run the test. The gateway response will appear here.
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
