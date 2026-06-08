"use client";

import { useCallback, useEffect, useState } from "react";
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from "recharts";
import { Activity, Calendar, Coins, Filter } from "lucide-react";
import { get } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

interface UsageRecord {
  id: string;
  timestamp: string;
  model_name: string;
  provider_name: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  billed_amount_usd: number;
  latency_ms: number | null;
  cache_hit: boolean;
  is_fallback: boolean;
  status: string;
}

interface UsageResponse {
  records: UsageRecord[];
  pagination: {
    page: number;
    page_size: number;
    total_count: number;
    total_pages: number;
  };
}

interface UsageSummaryItem {
  day: string;
  total_requests: number;
  total_tokens: number;
  total_cost: number;
  cache_hit_rate: number;
  error_rate: number;
}

export default function DailyUsagePage() {
  const [records, setRecords] = useState<UsageRecord[]>([]);
  const [summary, setSummary] = useState<UsageSummaryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Pagination and Filter state
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [totalCount, setTotalCount] = useState(0);
  const [modelFilter, setModelFilter] = useState("");

  const fetchData = useCallback(async (targetPage: number, model: string) => {
    try {
      setLoading(true);
      setError(null);
      
      // Fetch paginated usage records
      let url = `/api/usage?page=${targetPage}&page_size=15`;
      if (model) {
        url += `&model_name=${model}`;
      }
      const recordsData = await get<UsageResponse>(url);
      setRecords(recordsData.records);
      setPage(recordsData.pagination.page);
      setTotalPages(recordsData.pagination.total_pages);
      setTotalCount(recordsData.pagination.total_count);

      // Fetch daily summary
      const summaryData = await get<UsageSummaryItem[]>("/api/usage/summary");
      setSummary(summaryData);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load usage data");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData(1, modelFilter);
  }, [fetchData, modelFilter]);

  function formatDateTime(dateStr: string) {
    return new Date(dateStr).toLocaleString("en-US", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  }

  function formatChartDate(dateStr: string) {
    return new Date(dateStr).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
    });
  }

  // Format summary data for Recharts chart
  const chartData = summary.map(item => ({
    name: formatChartDate(item.day),
    Cost: parseFloat(item.total_cost.toFixed(4)),
    Requests: item.total_requests,
  }));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Usage Analytics</h1>
        <p className="text-sm text-muted-foreground">
          Analyze daily request volume, token usage, and cost overhead
        </p>
      </div>

      {error && (
        <div className="rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* Chart container */}
      <div className="rounded-xl border bg-card p-6 text-card-foreground shadow-sm">
        <h3 className="font-semibold text-lg leading-none tracking-tight mb-6">
          Daily Spend & Requests (Last 30 Days)
        </h3>
        
        {summary.length === 0 ? (
          <div className="flex h-[300px] items-center justify-center text-sm text-muted-foreground">
            No chart data available yet
          </div>
        ) : (
          <div className="h-[300px] w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="name" fontSize={11} tickLine={false} />
                <YAxis yAxisId="left" fontSize={11} tickLine={false} axisLine={false} unit="$" />
                <YAxis yAxisId="right" orientation="right" fontSize={11} tickLine={false} axisLine={false} />
                <Tooltip />
                <Legend />
                <Bar yAxisId="left" dataKey="Cost" fill="#10b981" radius={[4, 4, 0, 0]} name="Cost (USD)" />
                <Bar yAxisId="right" dataKey="Requests" fill="#3b82f6" radius={[4, 4, 0, 0]} name="Requests Count" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      {/* Usage Table Section */}
      <div className="rounded-xl border bg-card text-card-foreground shadow-sm">
        <div className="p-6 flex flex-col md:flex-row md:items-center justify-between border-b gap-4">
          <h3 className="font-semibold text-lg leading-none tracking-tight">
            Usage Records ({totalCount})
          </h3>
          
          {/* Filters */}
          <div className="flex items-center gap-3">
            <Filter className="h-4 w-4 text-muted-foreground" />
            <select
              className="rounded-md border border-input bg-transparent px-3 py-1.5 text-xs shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              value={modelFilter}
              onChange={(e) => {
                setModelFilter(e.target.value);
                setPage(1);
              }}
            >
              <option value="">All Models</option>
              <option value="llama-3.1-8b">Llama 3.1 8B</option>
              <option value="deepseek-chat">DeepSeek Chat</option>
              <option value="gemini-flash">Gemini 1.5 Flash</option>
            </select>
          </div>
        </div>

        {records.length === 0 ? (
          <div className="text-center py-12 text-sm text-muted-foreground">
            {modelFilter ? "No matching records found." : "No usage records available yet."}
          </div>
        ) : (
          <div>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Timestamp</TableHead>
                  <TableHead>Model</TableHead>
                  <TableHead>Provider</TableHead>
                  <TableHead>Tokens</TableHead>
                  <TableHead>Cost (USD)</TableHead>
                  <TableHead>Latency</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {records.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell className="text-sm text-muted-foreground">
                      {formatDateTime(r.timestamp)}
                    </TableCell>
                    <TableCell className="font-medium">{r.model_name}</TableCell>
                    <TableCell className="text-sm text-muted-foreground capitalize">
                      {r.provider_name}
                    </TableCell>
                    <TableCell className="text-sm">
                      {r.total_tokens.toLocaleString()}{" "}
                      <span className="text-xs text-muted-foreground">
                        ({r.input_tokens} / {r.output_tokens})
                      </span>
                    </TableCell>
                    <TableCell className="font-mono text-sm font-semibold">
                      {r.cache_hit ? (
                        <Badge variant="success">Cache Hit</Badge>
                      ) : (
                        `$${r.billed_amount_usd.toFixed(4)}`
                      )}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {r.latency_ms !== null ? `${r.latency_ms}ms` : "—"}
                    </TableCell>
                    <TableCell>
                      {r.is_fallback ? (
                        <Badge variant="warning">Fallback</Badge>
                      ) : (
                        <Badge variant="outline">Primary</Badge>
                      )}
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant={r.status === "success" ? "success" : "destructive"}
                      >
                        {r.status}
                      </Badge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>

            {/* Pagination Controls */}
            {totalPages > 1 && (
              <div className="flex items-center justify-between p-4 border-t">
                <span className="text-xs text-muted-foreground">
                  Showing page {page} of {totalPages}
                </span>
                <div className="flex items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={page === 1 || loading}
                    onClick={() => fetchData(page - 1, modelFilter)}
                  >
                    Previous
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={page === totalPages || loading}
                    onClick={() => fetchData(page + 1, modelFilter)}
                  >
                    Next
                  </Button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
