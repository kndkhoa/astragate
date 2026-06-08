"use client";

import { useCallback, useEffect, useState } from "react";
import { Search, User, CreditCard, Activity, Coins, ArrowLeft, History } from "lucide-react";
import { get } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

interface CustomerSummary {
  id: string;
  email: string;
  created_at: string;
  is_active: boolean;
  credit_balance: number;
  total_requests_30d: number;
  total_spend_30d: number;
}

interface CustomersResponse {
  customers: CustomerSummary[];
  pagination: {
    page: number;
    page_size: number;
    total_count: number;
    total_pages: number;
  };
}

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

interface CustomerDetail {
  id: string;
  email: string;
  created_at: string;
  is_active: boolean;
  credit_balance: number;
  last_topup_amount: number | null;
  last_topup_at: string | null;
  usage: {
    records: UsageRecord[];
    pagination: {
      page: number;
      page_size: number;
      total_count: number;
      total_pages: number;
    };
  };
}

export default function AdminCustomersPage() {
  const [customers, setCustomers] = useState<CustomerSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
  // Search and Pagination
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);

  // Selected Customer details
  const [selectedCustomerId, setSelectedCustomerId] = useState<string | null>(null);
  const [detail, setDetail] = useState<CustomerDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailPage, setDetailPage] = useState(1);

  const fetchCustomers = useCallback(async (targetPage: number, query: string) => {
    try {
      setLoading(true);
      setError(null);
      let url = `/admin/customers?page=${targetPage}&page_size=15`;
      if (query.trim()) {
        url += `&search=${encodeURIComponent(query.trim())}`;
      }
      const data = await get<CustomersResponse>(url);
      setCustomers(data.customers);
      setPage(data.pagination.page);
      setTotalPages(data.pagination.total_pages);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load customers");
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchCustomerDetail = useCallback(async (id: string, targetPage: number) => {
    try {
      setDetailLoading(true);
      const url = `/admin/customers/${id}?page=${targetPage}&page_size=10`;
      const data = await get<CustomerDetail>(url);
      setDetail(data);
      setDetailPage(data.usage.pagination.page);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to load customer details");
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchCustomers(1, search);
  }, [fetchCustomers, search]);

  useEffect(() => {
    if (selectedCustomerId) {
      fetchCustomerDetail(selectedCustomerId, 1);
    } else {
      setDetail(null);
    }
  }, [selectedCustomerId, fetchCustomerDetail]);

  function formatDate(dateStr: string) {
    return new Date(dateStr).toLocaleDateString("en-US", {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  }

  function formatDateTime(dateStr: string) {
    return new Date(dateStr).toLocaleString("en-US", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  // Render detail view if a customer is selected
  if (selectedCustomerId && detail) {
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-4">
          <Button variant="outline" size="icon" onClick={() => setSelectedCustomerId(null)}>
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div>
            <h1 className="text-2xl font-bold tracking-tight">{detail.email}</h1>
            <p className="text-sm text-muted-foreground">
              Joined on {formatDate(detail.created_at)}
            </p>
          </div>
        </div>

        {/* Customer KPI Summary */}
        <div className="grid gap-4 md:grid-cols-3">
          <div className="rounded-xl border bg-card p-6 text-card-foreground shadow-sm">
            <div className="flex items-center justify-between pb-2">
              <span className="text-sm font-medium text-muted-foreground">Credit Balance</span>
              <CreditCard className="h-4 w-4 text-muted-foreground" />
            </div>
            <div className="mt-1">
              <span className="text-3xl font-bold">${detail.credit_balance.toFixed(4)}</span>
              <span className="ml-1 text-sm text-muted-foreground">USD</span>
            </div>
            {detail.last_topup_at && (
              <p className="mt-2 text-xs text-muted-foreground">
                Last top-up: ${detail.last_topup_amount?.toFixed(2)} on{" "}
                {formatDate(detail.last_topup_at)}
              </p>
            )}
          </div>
          <div className="rounded-xl border bg-card p-6 text-card-foreground shadow-sm">
            <div className="flex items-center justify-between pb-2">
              <span className="text-sm font-medium text-muted-foreground">Total API Calls</span>
              <Activity className="h-4 w-4 text-muted-foreground" />
            </div>
            <div className="mt-1">
              <span className="text-3xl font-bold">
                {detail.usage.pagination.total_count.toLocaleString()}
              </span>
            </div>
          </div>
          <div className="rounded-xl border bg-card p-6 text-card-foreground shadow-sm">
            <div className="flex items-center justify-between pb-2">
              <span className="text-sm font-medium text-muted-foreground">Account Status</span>
              <User className="h-4 w-4 text-muted-foreground" />
            </div>
            <div className="mt-1.5">
              {detail.is_active ? (
                <Badge variant="success">Active Profile</Badge>
              ) : (
                <Badge variant="destructive">Suspended</Badge>
              )}
            </div>
          </div>
        </div>

        {/* Paginated Usage Records */}
        <div className="rounded-xl border bg-card text-card-foreground shadow-sm">
          <div className="p-6 border-b flex items-center gap-2">
            <History className="h-5 w-5 text-muted-foreground" />
            <h3 className="font-semibold text-lg leading-none tracking-tight">
              Customer Usage History
            </h3>
          </div>

          {detailLoading ? (
            <div className="flex items-center justify-center py-12">
              <div className="text-sm text-muted-foreground">Loading history...</div>
            </div>
          ) : detail.usage.records.length === 0 ? (
            <div className="text-center py-12 text-sm text-muted-foreground">
              No API requests recorded for this customer.
            </div>
          ) : (
            <div>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Time</TableHead>
                    <TableHead>Model</TableHead>
                    <TableHead>Provider</TableHead>
                    <TableHead>Tokens</TableHead>
                    <TableHead>Charge (USD)</TableHead>
                    <TableHead>Latency</TableHead>
                    <TableHead>Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {detail.usage.records.map((r) => (
                    <TableRow key={r.id}>
                      <TableCell className="text-sm text-muted-foreground font-mono">
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

              {/* Sub-pagination */}
              {detail.usage.pagination.total_pages > 1 && (
                <div className="flex items-center justify-between p-4 border-t">
                  <span className="text-xs text-muted-foreground">
                    Showing page {detailPage} of {detail.usage.pagination.total_pages}
                  </span>
                  <div className="flex items-center gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={detailPage === 1 || detailLoading}
                      onClick={() => fetchCustomerDetail(detail.id, detailPage - 1)}
                    >
                      Previous
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={detailPage === detail.usage.pagination.total_pages || detailLoading}
                      onClick={() => fetchCustomerDetail(detail.id, detailPage + 1)}
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

  return (
    <div className="space-y-6">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Customer Analytics</h1>
          <p className="text-sm text-muted-foreground">
            Search users, monitor balances, and audit historical LLM calls
          </p>
        </div>

        {/* Search Input */}
        <div className="relative w-full md:w-80">
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search by email..."
            className="pl-9"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
          />
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-12">
          <div className="text-sm text-muted-foreground">Loading customers...</div>
        </div>
      ) : customers.length === 0 ? (
        <div className="text-center py-12 text-sm text-muted-foreground">
          No customer records found matching your filters.
        </div>
      ) : (
        <div className="rounded-xl border bg-card text-card-foreground shadow-sm">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Customer Email</TableHead>
                <TableHead>Balance</TableHead>
                <TableHead>Requests (30d)</TableHead>
                <TableHead>Spend (30d)</TableHead>
                <TableHead>Created</TableHead>
                <TableHead>Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {customers.map((c) => (
                <TableRow
                  key={c.id}
                  className="cursor-pointer hover:bg-muted/50"
                  onClick={() => setSelectedCustomerId(c.id)}
                >
                  <TableCell className="font-semibold">{c.email}</TableCell>
                  <TableCell className="font-mono">${c.credit_balance.toFixed(2)}</TableCell>
                  <TableCell>{c.total_requests_30d.toLocaleString()}</TableCell>
                  <TableCell className="font-mono">${c.total_spend_30d.toFixed(4)}</TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {formatDate(c.created_at)}
                  </TableCell>
                  <TableCell>
                    {c.is_active ? (
                      <Badge variant="success">Active</Badge>
                    ) : (
                      <Badge variant="destructive">Suspended</Badge>
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
                  onClick={() => fetchCustomers(page - 1, search)}
                >
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page === totalPages}
                  onClick={() => fetchCustomers(page + 1, search)}
                >
                  Next
                </Button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
