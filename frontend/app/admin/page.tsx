"use client";

import { useCallback, useEffect, useState } from "react";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from "recharts";
import { Users, Coins, Activity, Server, AlertTriangle } from "lucide-react";
import { get, post } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

interface ProviderSummary {
  id: string;
  name: string;
  display_name: string;
  status: "normal" | "warning" | "hard_stop";
  balance_usd: number;
}

interface DailyRevenue {
  day: string;
  revenue: number;
  requests: number;
}

interface AdminOverviewResponse {
  total_customers: number;
  today_revenue: number;
  today_requests: number;
  providers: ProviderSummary[];
  daily_revenue_30d: DailyRevenue[];
}

export default function AdminOverviewPage() {
  const [data, setData] = useState<AdminOverviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [releasing, setReleasing] = useState<string | null>(null);

  const fetchOverview = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const res = await get<AdminOverviewResponse>("/admin/overview");
      setData(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load admin overview");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchOverview();
  }, [fetchOverview]);

  async function handleReleaseHardStop(providerId: string) {
    try {
      setReleasing(providerId);
      await post(`/admin/providers/${providerId}/release-hard-stop`, {});
      fetchOverview();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to release hard stop");
    } finally {
      setReleasing(null);
    }
  }

  function getStatusBadge(status: string) {
    switch (status) {
      case "normal":
        return <Badge variant="success">Normal</Badge>;
      case "warning":
        return <Badge variant="warning">Low Balance</Badge>;
      case "hard_stop":
        return <Badge variant="destructive">Hard Stopped</Badge>;
      default:
        return <Badge variant="outline">{status}</Badge>;
    }
  }

  function formatChartDate(dateStr: string) {
    return new Date(dateStr).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
    });
  }

  // Format revenue daily chart data
  const chartData = data?.daily_revenue_30d.map(item => ({
    name: formatChartDate(item.day),
    Revenue: parseFloat(item.revenue.toFixed(4)),
    Requests: item.requests,
  })) || [];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Admin Overview</h1>
        <p className="text-sm text-muted-foreground">
          Global platform stats, margins, and upstream provider balances
        </p>
      </div>

      {error && (
        <div className="rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {loading || !data ? (
        <div className="flex items-center justify-center py-12">
          <div className="text-sm text-muted-foreground">Loading overview...</div>
        </div>
      ) : (
        <div className="space-y-6">
          {/* KPI Card row */}
          <div className="grid gap-4 md:grid-cols-3">
            <div className="rounded-xl border bg-card p-6 text-card-foreground shadow-sm">
              <div className="flex items-center justify-between pb-2">
                <span className="text-sm font-medium text-muted-foreground">Total Customers</span>
                <Users className="h-4 w-4 text-muted-foreground" />
              </div>
              <div className="mt-1">
                <span className="text-3xl font-bold">{data.total_customers}</span>
              </div>
            </div>
            <div className="rounded-xl border bg-card p-6 text-card-foreground shadow-sm">
              <div className="flex items-center justify-between pb-2">
                <span className="text-sm font-medium text-muted-foreground">Today&apos;s Revenue</span>
                <Coins className="h-4 w-4 text-muted-foreground" />
              </div>
              <div className="mt-1">
                <span className="text-3xl font-bold">${data.today_revenue.toFixed(4)}</span>
                <span className="ml-1 text-sm text-muted-foreground">USD</span>
              </div>
            </div>
            <div className="rounded-xl border bg-card p-6 text-card-foreground shadow-sm">
              <div className="flex items-center justify-between pb-2">
                <span className="text-sm font-medium text-muted-foreground">Today&apos;s Requests</span>
                <Activity className="h-4 w-4 text-muted-foreground" />
              </div>
              <div className="mt-1">
                <span className="text-3xl font-bold">{data.today_requests.toLocaleString()}</span>
              </div>
            </div>
          </div>

          {/* Upstream Providers status row */}
          <div className="rounded-xl border bg-card p-6 text-card-foreground shadow-sm">
            <div className="flex items-center gap-2 mb-6">
              <Server className="h-5 w-5 text-muted-foreground" />
              <h3 className="font-semibold text-lg leading-none tracking-tight">
                Upstream Provider Status
              </h3>
            </div>
            
            <div className="grid gap-4 md:grid-cols-3">
              {data.providers.map((p) => (
                <div key={p.id} className="rounded-lg border p-4 flex flex-col justify-between space-y-4">
                  <div className="flex items-start justify-between">
                    <div>
                      <div className="font-semibold">{p.display_name}</div>
                      <code className="text-xs text-muted-foreground font-mono">{p.name}</code>
                    </div>
                    {getStatusBadge(p.status)}
                  </div>
                  
                  <div className="flex items-center justify-between pt-2 border-t">
                    <div>
                      <div className="text-xs text-muted-foreground">Balance</div>
                      <div className="font-mono font-semibold">${p.balance_usd.toFixed(2)} USD</div>
                    </div>

                    {p.status === "hard_stop" && (
                      <Button
                        size="sm"
                        variant="destructive"
                        className="h-8 text-xs"
                        onClick={() => handleReleaseHardStop(p.id)}
                        disabled={releasing === p.id}
                      >
                        {releasing === p.id ? "Releasing..." : "Release Stop"}
                      </Button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Daily Revenue Chart */}
          <div className="rounded-xl border bg-card p-6 text-card-foreground shadow-sm">
            <h3 className="font-semibold text-lg leading-none tracking-tight mb-6">
              Platform Daily Revenue (Last 30 Days)
            </h3>
            
            {data.daily_revenue_30d.length === 0 ? (
              <div className="flex h-[300px] items-center justify-center text-sm text-muted-foreground">
                No revenue history recorded yet
              </div>
            ) : (
              <div className="h-[300px] w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={chartData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} />
                    <XAxis dataKey="name" fontSize={11} tickLine={false} />
                    <YAxis fontSize={11} tickLine={false} axisLine={false} unit="$" />
                    <Tooltip />
                    <Legend />
                    <Line type="monotone" dataKey="Revenue" stroke="#10b981" strokeWidth={2.5} dot={false} name="Revenue (USD)" />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
