"use client";

import { useCallback, useEffect, useState } from "react";
import { CreditCard, History, Plus, ArrowUpRight, CheckCircle2, AlertCircle } from "lucide-react";
import { get, post } from "@/lib/api";
import { getDecodedToken } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

interface Transaction {
  id: string;
  type: string;
  amount_usd: number;
  balance_after: number;
  stripe_payment_intent_id: string | null;
  description: string | null;
  created_at: string;
}

interface TransactionsResponse {
  transactions: Transaction[];
  page: number;
  limit: number;
  total: number;
}

interface BalanceResponse {
  balance_usd: number;
}

interface TopupResponse {
  session_id: string;
  checkout_url: string;
  mock: boolean;
}

export default function BillingPage() {
  const [balance, setBalance] = useState<number | null>(null);
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [loading, setLoading] = useState(true);
  const [txLoading, setTxLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Top-up form state
  const [topupAmount, setTopupAmount] = useState<string>("10");
  const [customAmount, setCustomAmount] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [topupError, setTopupError] = useState<string | null>(null);

  // Pagination state
  const [page, setPage] = useState(1);
  const [totalTxs, setTotalTxs] = useState(0);
  const limit = 10;

  const fetchBalance = useCallback(async () => {
    try {
      const data = await get<BalanceResponse>("/api/billing/balance");
      setBalance(data.balance_usd);
    } catch (err) {
      console.error("Failed to load balance:", err);
    }
  }, []);

  const fetchTransactions = useCallback(async (pageNum = 1) => {
    try {
      setTxLoading(true);
      const data = await get<TransactionsResponse>(
        `/api/billing/transactions?page=${pageNum}&limit=${limit}`
      );
      setTransactions(data.transactions);
      setTotalTxs(data.total);
      setPage(data.page);
    } catch (err) {
      console.error("Failed to load transactions:", err);
    } finally {
      setTxLoading(false);
    }
  }, []);

  // Handle mock payment trigger if redirect back with mock url params
  useEffect(() => {
    if (typeof window === "undefined") return;

    const searchParams = new URLSearchParams(window.location.search);
    const mock = searchParams.get("mock_stripe_session");
    const amount = searchParams.get("amount");
    const sessionId = searchParams.get("session_id");

    if (mock && amount && sessionId) {
      const triggerMockPayment = async () => {
        try {
          const userId = getDecodedToken()?.sub;
          if (!userId) return;

          const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
          await fetch(`${API_URL}/api/billing/webhook`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              type: "payment_intent.succeeded",
              data: {
                object: {
                  id: `mock_pi_${sessionId}`,
                  metadata: {
                    user_id: userId,
                    amount: amount,
                  },
                },
              },
            }),
          });

          // Refresh data
          fetchBalance();
          fetchTransactions(1);
          // Remove query params from URL
          window.history.replaceState({}, "", window.location.pathname);
        } catch (err) {
          console.error("Mock Stripe processing failed:", err);
        }
      };
      triggerMockPayment();
    }
  }, [fetchBalance, fetchTransactions]);

  useEffect(() => {
    const loadData = async () => {
      setLoading(true);
      setError(null);
      try {
        await Promise.all([fetchBalance(), fetchTransactions(1)]);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load billing details");
      } finally {
        setLoading(false);
      }
    };
    loadData();

    // Subscribe to realtime balance updates
    let channel: any = null;
    const decoded = getDecodedToken();
    const userId = decoded?.sub;

    if (userId && process.env.NEXT_PUBLIC_SUPABASE_URL && process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY) {
      import("@/lib/supabase").then(({ supabase }) => {
        channel = supabase
          .channel("billing-balance")
          .on(
            "postgres_changes",
            {
              event: "UPDATE",
              schema: "public",
              table: "credit_accounts",
              filter: `user_id=eq.${userId}`,
            },
            (payload) => {
              if (payload.new && "balance_usd" in payload.new) {
                setBalance(Number(payload.new.balance_usd));
              }
              // Also refresh transactions list to show the new top-up
              fetchTransactions(1);
            }
          )
          .subscribe();
      });
    }

    return () => {
      if (channel) {
        channel.unsubscribe();
      }
    };
  }, [fetchBalance, fetchTransactions]);

  const handleTopup = async (e: React.FormEvent) => {
    e.preventDefault();
    setTopupError(null);
    setSubmitting(true);

    const amountStr = topupAmount === "custom" ? customAmount : topupAmount;
    const amountVal = parseFloat(amountStr);

    if (isNaN(amountVal) || amountVal < 5) {
      setTopupError("Minimum top-up amount is $5.00 USD");
      setSubmitting(false);
      return;
    }

    try {
      const res = await post<TopupResponse>("/api/billing/topup", {
        amount: amountVal,
      });
      // Redirect to Stripe Checkout page
      window.location.href = res.checkout_url;
    } catch (err) {
      setTopupError(err instanceof Error ? err.message : "Failed to create checkout session");
      setSubmitting(false);
    }
  };

  const getTxTypeBadge = (type: string) => {
    switch (type) {
      case "topup":
        return <Badge variant="success">Top-up</Badge>;
      case "usage":
        return <Badge variant="secondary">API Usage</Badge>;
      case "free_credit":
        return <Badge variant="success">Free Credit</Badge>;
      default:
        return <Badge variant="outline">{type}</Badge>;
    }
  };

  const formatDateTime = (dateStr: string) => {
    return new Date(dateStr).toLocaleString("en-US", {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  };

  const totalPages = Math.ceil(totalTxs / limit);

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-bold tracking-tight">Billing</h1>
        <p className="text-sm text-muted-foreground">
          Manage your prepaid credits, top up your account, and view transaction logs.
        </p>
      </div>

      {error && (
        <div className="mb-6 flex items-center gap-2 rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-12">
          <div className="text-sm text-muted-foreground">Loading billing details...</div>
        </div>
      ) : (
        <div className="grid gap-6 md:grid-cols-3">
          {/* Credit Balance Card */}
          <div className="rounded-xl border bg-card p-6 text-card-foreground shadow-sm md:col-span-1">
            <div className="flex items-center justify-between pb-2">
              <span className="text-sm font-medium text-muted-foreground">Credit Balance</span>
              <CreditCard className="h-4 w-4 text-muted-foreground" />
            </div>
            <div className="mt-1">
              <span className="text-3xl font-bold">
                ${balance !== null ? balance.toFixed(2) : "0.00"}
              </span>
              <span className="ml-1 text-sm text-muted-foreground">USD</span>
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              Credits do not expire. Usage will be deducted per request.
            </p>
          </div>

          {/* Top-up Card */}
          <div className="rounded-xl border bg-card p-6 text-card-foreground shadow-sm md:col-span-2">
            <h3 className="font-semibold text-lg leading-none tracking-tight mb-4">Add Credits</h3>
            <form onSubmit={handleTopup} className="space-y-4">
              <div className="space-y-2">
                <Label>Select Amount (USD)</Label>
                <div className="grid grid-cols-4 gap-2">
                  {["10", "25", "50"].map((amount) => (
                    <Button
                      key={amount}
                      type="button"
                      variant={topupAmount === amount ? "default" : "outline"}
                      className="w-full"
                      onClick={() => {
                        setTopupAmount(amount);
                        setTopupError(null);
                      }}
                    >
                      ${amount}
                    </Button>
                  ))}
                  <Button
                    type="button"
                    variant={topupAmount === "custom" ? "default" : "outline"}
                    className="w-full"
                    onClick={() => {
                      setTopupAmount("custom");
                      setTopupError(null);
                    }}
                  >
                    Custom
                  </Button>
                </div>
              </div>

              {topupAmount === "custom" && (
                <div className="space-y-1.5">
                  <Label htmlFor="custom-amount">Custom Amount *</Label>
                  <div className="relative">
                    <span className="absolute left-3 top-2.5 text-sm text-muted-foreground">$</span>
                    <Input
                      id="custom-amount"
                      type="number"
                      min="5"
                      step="1"
                      className="pl-7"
                      placeholder="Min $5.00"
                      value={customAmount}
                      onChange={(e) => {
                        setCustomAmount(e.target.value);
                        setTopupError(null);
                      }}
                      required
                    />
                  </div>
                </div>
              )}

              {topupError && (
                <div className="text-sm text-destructive">{topupError}</div>
              )}

              <Button type="submit" className="w-full" disabled={submitting}>
                <Plus className="mr-2 h-4 w-4" />
                {submitting ? "Redirecting..." : "Top Up Account"}
              </Button>
            </form>
          </div>

          {/* Transaction History */}
          <div className="rounded-xl border bg-card text-card-foreground shadow-sm md:col-span-3">
            <div className="p-6 flex items-center justify-between border-b">
              <div className="flex items-center gap-2">
                <History className="h-4 w-4 text-muted-foreground" />
                <h3 className="font-semibold text-lg leading-none tracking-tight">
                  Transaction History
                </h3>
              </div>
            </div>

            {txLoading ? (
              <div className="flex items-center justify-center py-12">
                <div className="text-sm text-muted-foreground">Loading transactions...</div>
              </div>
            ) : transactions.length === 0 ? (
              <div className="text-center py-12 text-sm text-muted-foreground">
                No transactions recorded yet.
              </div>
            ) : (
              <div>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Date</TableHead>
                      <TableHead>Type</TableHead>
                      <TableHead>Amount</TableHead>
                      <TableHead>Description</TableHead>
                      <TableHead>Payment Intent</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {transactions.map((tx) => (
                      <TableRow key={tx.id}>
                        <TableCell className="text-sm text-muted-foreground">
                          {formatDateTime(tx.created_at)}
                        </TableCell>
                        <TableCell>{getTxTypeBadge(tx.type)}</TableCell>
                        <TableCell className="font-mono">
                          <span
                            className={
                              tx.amount_usd > 0
                                ? "text-green-600 font-semibold"
                                : "text-muted-foreground"
                            }
                          >
                            {tx.amount_usd > 0 ? "+" : ""}
                            ${Math.abs(tx.amount_usd).toFixed(4)}
                          </span>
                        </TableCell>
                        <TableCell className="text-sm">{tx.description || "—"}</TableCell>
                        <TableCell className="text-sm text-muted-foreground font-mono">
                          {tx.stripe_payment_intent_id ? (
                            <span className="truncate max-w-[120px] block">
                              {tx.stripe_payment_intent_id}
                            </span>
                          ) : (
                            "—"
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>

                {/* Pagination */}
                {totalPages > 1 && (
                  <div className="flex items-center justify-between p-4 border-t">
                    <span className="text-xs text-muted-foreground">
                      Showing page {page} of {totalPages}
                    </span>
                    <div className="flex items-center gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={page === 1}
                        onClick={() => fetchTransactions(page - 1)}
                      >
                        Previous
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={page === totalPages}
                        onClick={() => fetchTransactions(page + 1)}
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
      )}
    </div>
  );
}
