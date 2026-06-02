"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, DollarSign, RefreshCw, TrendingDown } from "lucide-react";
import { get, put, post } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

// ── Types ────────────────────────────────────────────────────────────────────

interface ProviderItem {
  id: string;
  name: string;
  display_name: string;
  balance_usd: number;
  warning_threshold: number;
  hard_stop_threshold: number;
  status: "normal" | "warning" | "hard_stop";
  burn_rate_per_hour: number;
  burn_rate_per_day: number;
  days_remaining: number | null;
  is_active: boolean;
  hard_stop_activated_at: string | null;
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function statusVariant(status: string) {
  switch (status) {
    case "normal":
      return "success" as const;
    case "warning":
      return "warning" as const;
    case "hard_stop":
      return "destructive" as const;
    default:
      return "outline" as const;
  }
}

function statusLabel(status: string) {
  switch (status) {
    case "normal":
      return "Normal";
    case "warning":
      return "Warning";
    case "hard_stop":
      return "Hard Stop";
    default:
      return status;
  }
}

function formatUsd(amount: number): string {
  return `$${amount.toFixed(2)}`;
}

// ── Provider Card Component ──────────────────────────────────────────────────

function ProviderCard({
  provider,
  onRefresh,
}: {
  provider: ProviderItem;
  onRefresh: () => void;
}) {
  const [balanceInput, setBalanceInput] = useState("");
  const [updatingBalance, setUpdatingBalance] = useState(false);
  const [releasing, setReleasing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleUpdateBalance(e: React.FormEvent) {
    e.preventDefault();
    const amount = parseFloat(balanceInput);
    if (isNaN(amount) || amount <= 0) {
      setError("Enter a valid positive amount");
      return;
    }

    setUpdatingBalance(true);
    setError(null);
    try {
      await put(`/admin/providers/${provider.id}/balance`, {
        balance_usd: amount,
      });
      setBalanceInput("");
      onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update balance");
    } finally {
      setUpdatingBalance(false);
    }
  }

  async function handleReleaseHardStop() {
    setReleasing(true);
    setError(null);
    try {
      await post(`/admin/providers/${provider.id}/release-hard-stop`, {});
      onRefresh();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to release hard stop"
      );
    } finally {
      setReleasing(false);
    }
  }

  return (
    <Card
      className={cn(
        provider.status === "hard_stop" && "border-destructive/50",
        provider.status === "warning" && "border-yellow-500/50"
      )}
    >
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg">{provider.display_name}</CardTitle>
          <Badge variant={statusVariant(provider.status)}>
            {statusLabel(provider.status)}
          </Badge>
        </div>
        <CardDescription>{provider.name}</CardDescription>
      </CardHeader>

      <CardContent className="space-y-4">
        {/* Balance */}
        <div className="flex items-center gap-2">
          <DollarSign className="h-4 w-4 text-muted-foreground" />
          <span className="text-sm text-muted-foreground">Balance:</span>
          <span
            className={cn(
              "text-lg font-bold",
              provider.status === "hard_stop" && "text-destructive",
              provider.status === "warning" && "text-yellow-600"
            )}
          >
            {formatUsd(provider.balance_usd)}
          </span>
        </div>

        {/* Thresholds */}
        <div className="grid grid-cols-2 gap-3 text-sm">
          <div className="flex items-center gap-1.5">
            <AlertTriangle className="h-3.5 w-3.5 text-yellow-500" />
            <span className="text-muted-foreground">Warning:</span>
            <span className="font-medium">
              {formatUsd(provider.warning_threshold)}
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            <AlertTriangle className="h-3.5 w-3.5 text-destructive" />
            <span className="text-muted-foreground">Hard Stop:</span>
            <span className="font-medium">
              {formatUsd(provider.hard_stop_threshold)}
            </span>
          </div>
        </div>

        {/* Burn rate & days remaining */}
        <div className="grid grid-cols-2 gap-3 text-sm">
          <div className="flex items-center gap-1.5">
            <TrendingDown className="h-3.5 w-3.5 text-muted-foreground" />
            <span className="text-muted-foreground">Burn rate:</span>
            <span className="font-medium">
              {formatUsd(provider.burn_rate_per_hour)}/hr
            </span>
          </div>
          <div>
            <span className="text-muted-foreground">Days remaining:</span>{" "}
            <span className="font-medium">
              {provider.days_remaining !== null
                ? provider.days_remaining === Infinity || provider.days_remaining > 999
                  ? "∞"
                  : `${provider.days_remaining.toFixed(1)}d`
                : "N/A"}
            </span>
          </div>
        </div>

        {/* Error message */}
        {error && (
          <div className="rounded-md border border-destructive/50 bg-destructive/10 p-2 text-xs text-destructive">
            {error}
          </div>
        )}

        {/* Update Balance form */}
        <form onSubmit={handleUpdateBalance} className="space-y-2">
          <Label htmlFor={`balance-${provider.id}`} className="text-xs">
            Update Balance (USD)
          </Label>
          <div className="flex gap-2">
            <Input
              id={`balance-${provider.id}`}
              type="number"
              min="0"
              step="0.01"
              placeholder="New balance"
              value={balanceInput}
              onChange={(e) => setBalanceInput(e.target.value)}
              className="h-8 text-sm"
              disabled={updatingBalance}
            />
            <Button
              type="submit"
              size="sm"
              disabled={updatingBalance || !balanceInput}
            >
              {updatingBalance ? (
                <RefreshCw className="h-3.5 w-3.5 animate-spin" />
              ) : (
                "Update"
              )}
            </Button>
          </div>
        </form>
      </CardContent>

      {/* Release Hard Stop button — only visible when status=hard_stop */}
      {provider.status === "hard_stop" && (
        <CardFooter>
          <Button
            variant="destructive"
            className="w-full"
            onClick={handleReleaseHardStop}
            disabled={releasing}
          >
            {releasing ? (
              <RefreshCw className="mr-2 h-4 w-4 animate-spin" />
            ) : null}
            Release Hard Stop
          </Button>
        </CardFooter>
      )}
    </Card>
  );
}

// ── Page Component ───────────────────────────────────────────────────────────

export default function AdminProvidersPage() {
  const [providers, setProviders] = useState<ProviderItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchProviders = useCallback(async () => {
    try {
      setError(null);
      const data = await get<{ providers: ProviderItem[] }>(
        "/admin/providers"
      );
      setProviders(data.providers);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load providers"
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchProviders();
  }, [fetchProviders]);

  if (loading) {
    return (
      <div>
        <h1 className="mb-6 text-2xl font-bold tracking-tight">Providers</h1>
        <p className="text-muted-foreground">Loading providers...</p>
      </div>
    );
  }

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold tracking-tight">Providers</h1>
      <p className="mb-6 text-sm text-muted-foreground">
        Monitor provider balances, burn rates, and manage hard stop controls.
      </p>

      {error && (
        <div className="mb-4 rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {providers.length === 0 ? (
        <p className="text-muted-foreground">No providers configured.</p>
      ) : (
        <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
          {providers.map((provider) => (
            <ProviderCard
              key={provider.id}
              provider={provider}
              onRefresh={fetchProviders}
            />
          ))}
        </div>
      )}
    </div>
  );
}
