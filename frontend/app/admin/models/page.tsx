"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, Pencil, X } from "lucide-react";
import { get, put } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

// ── Types ────────────────────────────────────────────────────────────────────

interface ModelItem {
  id: string;
  model_id: string;
  display_name: string;
  provider_name: string;
  input_price_per_1m: number;
  output_price_per_1m: number;
  markup_rate: number;
  markup_source: "model" | "provider" | "global";
  effective_input_price: number;
  effective_output_price: number;
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function formatPrice(price: number): string {
  if (price < 0.01) {
    return `$${price.toFixed(4)}`;
  }
  return `$${price.toFixed(2)}`;
}

function markupSourceVariant(source: string) {
  switch (source) {
    case "model":
      return "default" as const;
    case "provider":
      return "secondary" as const;
    case "global":
      return "outline" as const;
    default:
      return "outline" as const;
  }
}

// ── Page Component ───────────────────────────────────────────────────────────

export default function AdminModelsPage() {
  const [models, setModels] = useState<ModelItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Inline edit state
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [saving, setSaving] = useState(false);

  const fetchModels = useCallback(async () => {
    try {
      setError(null);
      const data = await get<{ models: ModelItem[] }>("/admin/models");
      setModels(data.models);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load models");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchModels();
  }, [fetchModels]);

  function startEdit(model: ModelItem) {
    setEditingId(model.id);
    // Show current markup as percentage (e.g. 0.20 → 20)
    setEditValue((model.markup_rate * 100).toFixed(0));
  }

  function cancelEdit() {
    setEditingId(null);
    setEditValue("");
  }

  async function saveMarkup(modelId: string) {
    const ratePercent = parseFloat(editValue);
    if (isNaN(ratePercent) || ratePercent < 0 || ratePercent > 500) {
      return;
    }

    const markupRate = ratePercent / 100; // Convert percentage to decimal

    setSaving(true);
    try {
      await put(`/admin/models/${modelId}/markup`, {
        markup_rate: markupRate === 0 ? 0 : markupRate || null,
      });
      setEditingId(null);
      setEditValue("");
      await fetchModels();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update markup");
    } finally {
      setSaving(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent, modelId: string) {
    if (e.key === "Enter") {
      saveMarkup(modelId);
    } else if (e.key === "Escape") {
      cancelEdit();
    }
  }

  if (loading) {
    return (
      <div>
        <h1 className="mb-6 text-2xl font-bold tracking-tight">Models</h1>
        <p className="text-muted-foreground">Loading models...</p>
      </div>
    );
  }

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold tracking-tight">Models</h1>
      <p className="mb-4 text-sm text-muted-foreground">
        Manage markup rates for each model. Effective sell price = base price ×
        (1 + markup rate).
      </p>

      {error && (
        <div className="mb-4 rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Model</TableHead>
              <TableHead>Provider</TableHead>
              <TableHead className="text-right">
                Input / 1M tokens
              </TableHead>
              <TableHead className="text-right">
                Output / 1M tokens
              </TableHead>
              <TableHead>Markup Source</TableHead>
              <TableHead className="text-right">Markup Rate</TableHead>
              <TableHead className="text-right">
                Sell (Input / 1M)
              </TableHead>
              <TableHead className="text-right">
                Sell (Output / 1M)
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {models.length === 0 ? (
              <TableRow>
                <TableCell colSpan={8} className="text-center text-muted-foreground">
                  No models configured.
                </TableCell>
              </TableRow>
            ) : (
              models.map((model) => (
                <TableRow key={model.id}>
                  <TableCell className="font-medium">
                    {model.display_name}
                    <span className="ml-2 text-xs text-muted-foreground">
                      {model.model_id}
                    </span>
                  </TableCell>
                  <TableCell>{model.provider_name}</TableCell>
                  <TableCell className="text-right font-mono text-sm">
                    {formatPrice(model.input_price_per_1m)}
                  </TableCell>
                  <TableCell className="text-right font-mono text-sm">
                    {formatPrice(model.output_price_per_1m)}
                  </TableCell>
                  <TableCell>
                    <Badge variant={markupSourceVariant(model.markup_source)}>
                      {model.markup_source}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-right">
                    {editingId === model.id ? (
                      <div className="flex items-center justify-end gap-1">
                        <Input
                          type="number"
                          min="0"
                          max="500"
                          step="1"
                          value={editValue}
                          onChange={(e) => setEditValue(e.target.value)}
                          onKeyDown={(e) => handleKeyDown(e, model.id)}
                          className="h-7 w-20 text-right text-sm"
                          autoFocus
                          disabled={saving}
                          aria-label="Markup rate percentage"
                        />
                        <span className="text-xs text-muted-foreground">%</span>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          onClick={() => saveMarkup(model.id)}
                          disabled={saving}
                          aria-label="Save markup"
                        >
                          <Check className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          onClick={cancelEdit}
                          disabled={saving}
                          aria-label="Cancel edit"
                        >
                          <X className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    ) : (
                      <div className="flex items-center justify-end gap-1">
                        <span className="font-mono text-sm">
                          {(model.markup_rate * 100).toFixed(0)}%
                        </span>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          onClick={() => startEdit(model)}
                          aria-label={`Edit markup for ${model.display_name}`}
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    )}
                  </TableCell>
                  <TableCell className="text-right font-mono text-sm text-green-600">
                    {formatPrice(model.effective_input_price)}
                  </TableCell>
                  <TableCell className="text-right font-mono text-sm text-green-600">
                    {formatPrice(model.effective_output_price)}
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
