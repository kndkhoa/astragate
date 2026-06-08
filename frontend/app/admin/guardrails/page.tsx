"use client";

import { useCallback, useEffect, useState } from "react";
import { Plus, Trash2, ShieldAlert, Check, Shield } from "lucide-react";
import { get, post, put, del } from "@/lib/api";
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface GuardrailKeyword {
  id: string;
  keyword: string;
  scope: "input" | "output" | "both";
  created_at: string;
  event_count_7d: number;
}

interface GuardrailsResponse {
  keywords: GuardrailKeyword[];
  total_events_7d: number;
}

export default function AdminGuardrailsPage() {
  const [keywords, setKeywords] = useState<GuardrailKeyword[]>([]);
  const [totalEvents, setTotalEvents] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Add keyword state
  const [addOpen, setAddOpen] = useState(false);
  const [newKeyword, setNewKeyword] = useState("");
  const [newScope, setNewScope] = useState<"input" | "output" | "both">("both");
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  // Delete keyword state
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<GuardrailKeyword | null>(null);
  const [deleting, setDeleting] = useState(false);

  // Edit keyword state
  const [editOpen, setEditOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<GuardrailKeyword | null>(null);
  const [editKeyword, setEditKeyword] = useState("");
  const [editScope, setEditScope] = useState<"input" | "output" | "both">("both");
  const [editing, setEditing] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  const fetchGuardrails = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await get<GuardrailsResponse>("/admin/guardrails");
      setKeywords(data.keywords);
      setTotalEvents(data.total_events_7d);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load guardrails");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchGuardrails();
  }, [fetchGuardrails]);

  async function handleAddKeyword(e: React.FormEvent) {
    e.preventDefault();
    if (!newKeyword.trim()) return;

    try {
      setAdding(true);
      setAddError(null);
      await post("/admin/guardrails", {
        keyword: newKeyword.trim(),
        scope: newScope,
      });
      setAddOpen(false);
      setNewKeyword("");
      setNewScope("both");
      fetchGuardrails();
    } catch (err) {
      setAddError(err instanceof Error ? err.message : "Failed to add keyword");
    } finally {
      setAdding(false);
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    try {
      setDeleting(true);
      await del(`/admin/guardrails/${deleteTarget.id}`);
      setDeleteOpen(false);
      setDeleteTarget(null);
      fetchGuardrails();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete keyword");
    } finally {
      setDeleting(false);
    }
  }

  async function handleEditKeyword(e: React.FormEvent) {
    e.preventDefault();
    if (!editTarget || !editKeyword.trim()) return;

    try {
      setEditing(true);
      setEditError(null);
      await put(`/admin/guardrails/${editTarget.id}`, {
        keyword: editKeyword.trim(),
        scope: editScope,
      });
      setEditOpen(false);
      setEditTarget(null);
      setEditKeyword("");
      setEditScope("both");
      fetchGuardrails();
    } catch (err) {
      setEditError(err instanceof Error ? err.message : "Failed to edit keyword");
    } finally {
      setEditing(false);
    }
  }

  function getScopeBadge(scope: string) {
    switch (scope) {
      case "input":
        return <Badge variant="outline">Input Only</Badge>;
      case "output":
        return <Badge variant="outline">Output Only</Badge>;
      default:
        return <Badge variant="secondary">Both</Badge>;
    }
  }

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Content Guardrails</h1>
          <p className="text-sm text-muted-foreground">
            Manage banned keywords and monitor policy violation events
          </p>
        </div>
        <Button onClick={() => setAddOpen(true)}>
          <Plus className="mr-2 h-4 w-4" />
          Add Keyword
        </Button>
      </div>

      {/* KPI Card row */}
      <div className="grid gap-4 md:grid-cols-3 mb-6">
        <div className="rounded-xl border bg-card p-6 text-card-foreground shadow-sm">
          <div className="flex items-center justify-between pb-2">
            <span className="text-sm font-medium text-muted-foreground">Total Banned Keywords</span>
            <Shield className="h-4 w-4 text-muted-foreground" />
          </div>
          <div className="mt-1">
            <span className="text-3xl font-bold">{keywords.length}</span>
          </div>
        </div>
        <div className="rounded-xl border bg-card p-6 text-card-foreground shadow-sm">
          <div className="flex items-center justify-between pb-2">
            <span className="text-sm font-medium text-muted-foreground">Violations Triggered (7d)</span>
            <ShieldAlert className="h-4 w-4 text-destructive" />
          </div>
          <div className="mt-1">
            <span className="text-3xl font-bold text-destructive">{totalEvents}</span>
          </div>
        </div>
      </div>

      {error && (
        <div className="mb-4 rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-12">
          <div className="text-sm text-muted-foreground">Loading guardrails...</div>
        </div>
      ) : keywords.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-lg border border-dashed py-12">
          <Shield className="mb-3 h-10 w-10 text-muted-foreground/50" />
          <p className="text-sm text-muted-foreground">No banned keywords configured yet</p>
          <Button
            variant="outline"
            size="sm"
            className="mt-3"
            onClick={() => setAddOpen(true)}
          >
            <Plus className="mr-2 h-3 w-3" />
            Add your first keyword
          </Button>
        </div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Banned Keyword</TableHead>
              <TableHead>Scope</TableHead>
              <TableHead>Violations (Last 7 Days)</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {keywords.map((kw) => (
              <TableRow key={kw.id}>
                <TableCell>
                  <code className="rounded bg-muted px-2 py-1 text-sm font-semibold font-mono">
                    {kw.keyword}
                  </code>
                </TableCell>
                <TableCell>{getScopeBadge(kw.scope)}</TableCell>
                <TableCell className="font-semibold text-sm">
                  {kw.event_count_7d > 0 ? (
                    <span className="text-destructive font-bold">{kw.event_count_7d}</span>
                  ) : (
                    <span className="text-muted-foreground">0</span>
                  )}
                </TableCell>
                <TableCell>
                  <Badge variant="success">Active</Badge>
                </TableCell>
                <TableCell className="text-right">
                  <div className="flex justify-end gap-2">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        setEditTarget(kw);
                        setEditKeyword(kw.keyword);
                        setEditScope(kw.scope);
                        setEditOpen(true);
                      }}
                    >
                      Edit
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-destructive hover:text-destructive"
                      onClick={() => {
                        setDeleteTarget(kw);
                        setDeleteOpen(true);
                      }}
                    >
                      <Trash2 className="mr-1 h-3.5 w-3.5" />
                      Delete
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      {/* Add Keyword Dialog */}
      <Dialog
        open={addOpen}
        onOpenChange={(open) => {
          setAddOpen(open);
          if (!open) {
            setNewKeyword("");
            setNewScope("both");
            setAddError(null);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add Banned Keyword</DialogTitle>
            <DialogDescription>
              Any chat request or output matching this exact keyword will be rejected by content policy.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleAddKeyword} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="keyword-text">Keyword *</Label>
              <Input
                id="keyword-text"
                placeholder="e.g. hack, exploit, bypass"
                value={newKeyword}
                onChange={(e) => setNewKeyword(e.target.value)}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="scope-select">Policy Scope</Label>
              <select
                id="scope-select"
                className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                value={newScope}
                onChange={(e) => setNewScope(e.target.value as any)}
              >
                <option value="both">Both (Input & Output)</option>
                <option value="input">Input Only (Prompts)</option>
                <option value="output">Output Only (Completions)</option>
              </select>
            </div>
            {addError && (
              <div className="text-sm text-destructive">{addError}</div>
            )}
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setAddOpen(false)}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={adding || !newKeyword.trim()}>
                {adding ? "Adding..." : "Add Keyword"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Edit Keyword Dialog */}
      <Dialog
        open={editOpen}
        onOpenChange={(open) => {
          setEditOpen(open);
          if (!open) {
            setEditTarget(null);
            setEditKeyword("");
            setEditScope("both");
            setEditError(null);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit Banned Keyword</DialogTitle>
            <DialogDescription>
              Update the banned keyword and its scope.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleEditKeyword} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="edit-keyword-text">Keyword *</Label>
              <Input
                id="edit-keyword-text"
                placeholder="e.g. hack, exploit, bypass"
                value={editKeyword}
                onChange={(e) => setEditKeyword(e.target.value)}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="edit-scope-select">Policy Scope</Label>
              <select
                id="edit-scope-select"
                className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                value={editScope}
                onChange={(e) => setEditScope(e.target.value as any)}
              >
                <option value="both">Both (Input & Output)</option>
                <option value="input">Input Only (Prompts)</option>
                <option value="output">Output Only (Completions)</option>
              </select>
            </div>
            {editError && (
              <div className="text-sm text-destructive">{editError}</div>
            )}
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setEditOpen(false)}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={editing || !editKeyword.trim()}>
                {editing ? "Saving..." : "Save Changes"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Banned Keyword</DialogTitle>
            <DialogDescription>
              Are you sure you want to remove the banned keyword &quot;{deleteTarget?.keyword}&quot;?
              This keyword will no longer be checked in the gateway pipeline.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setDeleteOpen(false);
                setDeleteTarget(null);
              }}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleDelete}
              disabled={deleting}
            >
              {deleting ? "Deleting..." : "Delete Keyword"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
