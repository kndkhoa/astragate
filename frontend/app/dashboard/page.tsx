"use client";

import { useCallback, useEffect, useState } from "react";
import { CreditCard, Activity, Coins, ArrowUpRight, Shield } from "lucide-react";
import { get } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import Link from "next/link";

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
    total_count: number;
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

export default function DashboardOverviewPage() {
  const [balance, setBalance] = useState<number | null>(null);
  const [recentCalls, setRecentCalls] = useState<UsageRecord[]>([]);
  const [todayCost, setTodayCost] = useState(0);
  const [todayTokens, setTodayTokens] = useState(0);
  const [todayRequests, setTodayRequests] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchBalance = useCallback(async () => {
    try {
      const data = await get<{ balance_usd: number }>("/api/billing/balance");
      setBalance(data.balance_usd);
    } catch (err) {
      console.error("Failed to fetch balance", err);
    }
  }, []);

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      
      // Fetch credit balance
      await fetchBalance();

      // Fetch recent 5 API calls
      const usageData = await get<UsageResponse>("/api/usage?page=1&page_size=5");
      setRecentCalls(usageData.records);

      // Fetch daily summary to get today's stats
      const summary = await get<UsageSummaryItem[]>("/api/usage/summary");
      if (summary && summary.length > 0) {
        // Find today's date in local YYYY-MM-DD
        const todayStr = new Date().toISOString().split("T")[0];
        const todayItem = summary.find(item => item.day === todayStr) || summary[summary.length - 1];
        if (todayItem) {
          setTodayCost(todayItem.total_cost);
          setTodayTokens(todayItem.total_tokens);
          setTodayRequests(todayItem.total_requests);
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load dashboard data");
    } finally {
      setLoading(false);
    }
  }, [fetchBalance]);

  useEffect(() => {
    fetchData();

    // Auto-refresh credit balance every 60 seconds (Task 33)
    const interval = setInterval(() => {
      fetchBalance();
    }, 60000);

    return () => clearInterval(interval);
  }, [fetchData, fetchBalance]);

  function formatDateTime(dateStr: string) {
    return new Date(dateStr).toLocaleString("en-US", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  }

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Overview</h1>
          <p className="text-sm text-muted-foreground">
            Monitor credit balance and gateway activities in real-time
          </p>
        </div>
      </div>

      {error && (
        <div className="mb-4 rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-12">
          <div className="text-sm text-muted-foreground">Loading dashboard data...</div>
        </div>
      ) : (
        <div className="space-y-6">
          {/* Card row */}
          <div className="grid gap-4 md:grid-cols-4">
            {/* Credit Balance Card */}
            <div className="rounded-xl border bg-card p-6 text-card-foreground shadow-sm">
              <div className="flex items-center justify-between pb-2">
                <span className="text-sm font-medium text-muted-foreground">Credit Balance</span>
                <CreditCard className="h-4 w-4 text-muted-foreground" />
              </div>
              <div className="mt-1">
                <span className="text-3xl font-bold">
                  ${balance !== null ? balance.toFixed(4) : "0.0000"}
                </span>
                <span className="ml-1 text-sm text-muted-foreground">USD</span>
              </div>
              <div className="mt-4 flex items-center justify-between">
                <span className="text-xs text-muted-foreground">Auto-refreshes every 60s</span>
                <Link href="/dashboard/billing">
                  <Button size="sm" variant="ghost" className="h-7 px-2">
                    Billing <ArrowUpRight className="ml-1 h-3 w-3" />
                  </Button>
                </Link>
              </div>
            </div>

            {/* Today's Spend Card */}
            <div className="rounded-xl border bg-card p-6 text-card-foreground shadow-sm">
              <div className="flex items-center justify-between pb-2">
                <span className="text-sm font-medium text-muted-foreground">Today&apos;s Spend</span>
                <Coins className="h-4 w-4 text-muted-foreground" />
              </div>
              <div className="mt-1">
                <span className="text-3xl font-bold">${todayCost.toFixed(4)}</span>
                <span className="ml-1 text-sm text-muted-foreground">USD</span>
              </div>
            </div>

            {/* Today's Requests Card */}
            <div className="rounded-xl border bg-card p-6 text-card-foreground shadow-sm">
              <div className="flex items-center justify-between pb-2">
                <span className="text-sm font-medium text-muted-foreground">Today&apos;s Requests</span>
                <Activity className="h-4 w-4 text-muted-foreground" />
              </div>
              <div className="mt-1">
                <span className="text-3xl font-bold">{todayRequests.toLocaleString()}</span>
              </div>
            </div>

            {/* Today's Tokens Card */}
            <div className="rounded-xl border bg-card p-6 text-card-foreground shadow-sm">
              <div className="flex items-center justify-between pb-2">
                <span className="text-sm font-medium text-muted-foreground">Today&apos;s Tokens</span>
                <Coins className="h-4 w-4 text-muted-foreground" />
              </div>
              <div className="mt-1">
                <span className="text-3xl font-bold">{todayTokens.toLocaleString()}</span>
              </div>
            </div>
          </div>

          {/* Recent API Calls */}
          <div className="rounded-xl border bg-card text-card-foreground shadow-sm">
            <div className="p-6 flex items-center justify-between border-b">
              <h3 className="font-semibold text-lg leading-none tracking-tight">
                Recent API Calls
              </h3>
              <Link href="/dashboard/usage">
                <Button size="sm" variant="outline">
                  View All Usage
                </Button>
              </Link>
            </div>

            {recentCalls.length === 0 ? (
              <div className="text-center py-12 text-sm text-muted-foreground">
                No API calls recorded yet. Go to the{" "}
                <Link href="/dashboard/quickstart" className="underline font-medium">
                  Quick Start
                </Link>{" "}
                page to make your first call.
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Time</TableHead>
                    <TableHead>Model</TableHead>
                    <TableHead>Provider</TableHead>
                    <TableHead>Tokens</TableHead>
                    <TableHead>Cost</TableHead>
                    <TableHead>Latency</TableHead>
                    <TableHead>Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {recentCalls.map((call) => (
                    <TableRow key={call.id}>
                      <TableCell className="text-sm text-muted-foreground">
                        {formatDateTime(call.timestamp)}
                      </TableCell>
                      <TableCell className="font-medium">{call.model_name}</TableCell>
                      <TableCell className="text-sm text-muted-foreground capitalize">
                        {call.provider_name}
                      </TableCell>
                      <TableCell className="text-sm">
                        {call.total_tokens.toLocaleString()}{" "}
                        <span className="text-xs text-muted-foreground">
                          ({call.input_tokens} / {call.output_tokens})
                        </span>
                      </TableCell>
                      <TableCell className="font-mono text-sm">
                        {call.cache_hit ? (
                          <Badge variant="success">Cache Hit</Badge>
                        ) : (
                          `$${call.billed_amount_usd.toFixed(4)}`
                        )}
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {call.latency_ms !== null ? `${call.latency_ms}ms` : "—"}
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant={call.status === "success" ? "success" : "destructive"}
                        >
                          {call.status}
                        </Badge>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
