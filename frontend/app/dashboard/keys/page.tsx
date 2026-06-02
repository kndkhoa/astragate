"use client";

import { useCallback, useEffect, useState } from "react";
import { Key, Plus, Copy, Trash2, Check, AlertTriangle } from "lucide-react";
import { get, post, del } from "@/lib/api";
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

interface VirtualKey {
  id: string;
  name: string;
  description: string | null;
  key_prefix: string;
  is_active: boolean;
  rate_limit_rpm: number | null;
  last_used_at: string | null;
  total_requests: number;
  total_tokens: number;
  created_at: string;
  revoked_at: string | null;
}

interface KeysResponse {
  keys: VirtualKey[];
  total: number;
}

interface CreateKeyResponse extends VirtualKey {
  key: string;
}

export default function ApiKeysPage() {
  const [keys, setKeys] = useState<VirtualKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Create key modal state
  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createDescription, setCreateDescription] = useState("");
  const [createRpm, setCreateRpm] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  // Key reveal modal state
  const [revealOpen, setRevealOpen] = useState(false);
  const [revealedKey, setRevealedKey] = useState("");
  const [copied, setCopied] = useState(false);

  // Revoke confirmation modal state
  const [revokeOpen, setRevokeOpen] = useState(false);
  const [revokeTarget, setRevokeTarget] = useState<VirtualKey | null>(null);
  const [revoking, setRevoking] = useState(false);

  const fetchKeys = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await get<KeysResponse>("/api/keys");
      setKeys(data.keys);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load keys");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchKeys();
  }, [fetchKeys]);

  async function handleCreateKey(e: React.FormEvent) {
    e.preventDefault();
    if (!createName.trim()) return;

    try {
      setCreating(true);
      setCreateError(null);
      const body: { name: string; description?: string; rate_limit_rpm?: number } = {
        name: createName.trim(),
      };
      if (createDescription.trim()) {
        body.description = createDescription.trim();
      }
      if (createRpm.trim()) {
        const rpm = parseInt(createRpm, 10);
        if (isNaN(rpm) || rpm < 1) {
          setCreateError("RPM limit must be a positive number");
          setCreating(false);
          return;
        }
        body.rate_limit_rpm = rpm;
      }

      const result = await post<CreateKeyResponse>("/api/keys", body);
      setRevealedKey(result.key);
      setCreateOpen(false);
      setRevealOpen(true);
      resetCreateForm();
      fetchKeys();
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "Failed to create key");
    } finally {
      setCreating(false);
    }
  }

  function resetCreateForm() {
    setCreateName("");
    setCreateDescription("");
    setCreateRpm("");
    setCreateError(null);
  }

  async function handleRevoke() {
    if (!revokeTarget) return;
    try {
      setRevoking(true);
      await del(`/api/keys/${revokeTarget.id}`);
      setRevokeOpen(false);
      setRevokeTarget(null);
      fetchKeys();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to revoke key");
    } finally {
      setRevoking(false);
    }
  }

  async function handleCopyKey() {
    try {
      await navigator.clipboard.writeText(revealedKey);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Fallback for environments without clipboard API
      const textarea = document.createElement("textarea");
      textarea.value = revealedKey;
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      document.body.removeChild(textarea);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  }

  function formatDate(dateStr: string) {
    return new Date(dateStr).toLocaleDateString("en-US", {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  }

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">API Keys</h1>
          <p className="text-sm text-muted-foreground">
            Create and manage your virtual API keys
          </p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="mr-2 h-4 w-4" />
          Create Key
        </Button>
      </div>

      {error && (
        <div className="mb-4 rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-12">
          <div className="text-sm text-muted-foreground">Loading keys...</div>
        </div>
      ) : keys.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-lg border border-dashed py-12">
          <Key className="mb-3 h-10 w-10 text-muted-foreground/50" />
          <p className="text-sm text-muted-foreground">No API keys yet</p>
          <Button
            variant="outline"
            size="sm"
            className="mt-3"
            onClick={() => setCreateOpen(true)}
          >
            <Plus className="mr-2 h-3 w-3" />
            Create your first key
          </Button>
        </div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Key</TableHead>
              <TableHead>Name</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Created</TableHead>
              <TableHead>Requests</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {keys.map((key) => (
              <TableRow key={key.id}>
                <TableCell>
                  <code className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono">
                    {key.key_prefix}...
                  </code>
                </TableCell>
                <TableCell>
                  <div className="font-medium">{key.name}</div>
                  {key.description && (
                    <div className="text-xs text-muted-foreground">
                      {key.description}
                    </div>
                  )}
                </TableCell>
                <TableCell>
                  {key.is_active ? (
                    <Badge variant="success">Active</Badge>
                  ) : (
                    <Badge variant="destructive">Revoked</Badge>
                  )}
                </TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {formatDate(key.created_at)}
                </TableCell>
                <TableCell className="text-sm">
                  {key.total_requests.toLocaleString()}
                </TableCell>
                <TableCell className="text-right">
                  {key.is_active && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-destructive hover:text-destructive"
                      onClick={() => {
                        setRevokeTarget(key);
                        setRevokeOpen(true);
                      }}
                    >
                      <Trash2 className="mr-1 h-3.5 w-3.5" />
                      Revoke
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      {/* Create Key Dialog */}
      <Dialog
        open={createOpen}
        onOpenChange={(open) => {
          setCreateOpen(open);
          if (!open) resetCreateForm();
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create API Key</DialogTitle>
            <DialogDescription>
              Create a new virtual API key for accessing the gateway.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleCreateKey} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="key-name">Name *</Label>
              <Input
                id="key-name"
                placeholder="e.g. Production, Development"
                value={createName}
                onChange={(e) => setCreateName(e.target.value)}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="key-description">Description</Label>
              <Input
                id="key-description"
                placeholder="Optional description"
                value={createDescription}
                onChange={(e) => setCreateDescription(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="key-rpm">Rate Limit (RPM)</Label>
              <Input
                id="key-rpm"
                type="number"
                min="1"
                placeholder="Leave empty for unlimited"
                value={createRpm}
                onChange={(e) => setCreateRpm(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                Maximum requests per minute. Leave empty for no limit.
              </p>
            </div>
            {createError && (
              <div className="text-sm text-destructive">{createError}</div>
            )}
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setCreateOpen(false)}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={creating || !createName.trim()}>
                {creating ? "Creating..." : "Create Key"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Key Reveal Dialog */}
      <Dialog
        open={revealOpen}
        onOpenChange={(open) => {
          setRevealOpen(open);
          if (!open) {
            setRevealedKey("");
            setCopied(false);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Key Created Successfully</DialogTitle>
            <DialogDescription>
              Copy your API key now. You won&apos;t be able to see it again.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="flex items-center gap-2 rounded-md border bg-muted p-3">
              <code className="flex-1 break-all text-sm font-mono">
                {revealedKey}
              </code>
              <Button
                variant="outline"
                size="icon"
                className="shrink-0"
                onClick={handleCopyKey}
              >
                {copied ? (
                  <Check className="h-4 w-4 text-green-600" />
                ) : (
                  <Copy className="h-4 w-4" />
                )}
              </Button>
            </div>
            <div className="flex items-start gap-2 rounded-md border border-yellow-200 bg-yellow-50 p-3 text-sm text-yellow-800">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>
                This key will only be shown once. Store it securely — you cannot
                retrieve it later.
              </span>
            </div>
          </div>
          <DialogFooter>
            <Button onClick={() => setRevealOpen(false)}>Done</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Revoke Confirmation Dialog */}
      <Dialog open={revokeOpen} onOpenChange={setRevokeOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Revoke API Key</DialogTitle>
            <DialogDescription>
              Are you sure you want to revoke{" "}
              <span className="font-medium text-foreground">
                {revokeTarget?.name}
              </span>
              ? This action cannot be undone. All requests using this key will be
              rejected immediately.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setRevokeOpen(false);
                setRevokeTarget(null);
              }}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleRevoke}
              disabled={revoking}
            >
              {revoking ? "Revoking..." : "Revoke Key"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
