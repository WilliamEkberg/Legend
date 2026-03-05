// Doc: Natural_Language_Code/Frontend/info_frontend.md

import { useState, useEffect, useRef } from "react";
import type { SelectedNode, MapDecision, MapModule, MapComponent, ChangeRecord, ValidationSummary, DecisionValidation } from "../../data/types";
import { updateDecision, createDecision, deleteDecision, deleteModule, deleteComponent } from "../../api/client";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface DetailPanelProps {
  selected: SelectedNode;
  onClose: () => void;
  onDecisionChange: (
    kind: "module" | "component",
    entityId: number,
    newDecisions: MapDecision[],
  ) => void;
  changeRecords: ChangeRecord[];
  validationSummary: ValidationSummary | null;
  onMutate: () => void;
  onDelete: () => void;
}

const DECISION_CATEGORIES = [
  "api_contracts",
  "patterns",
  "libraries",
  "boundaries",
  "error_handling",
  "data_flow",
  "cross_cutting",
  "deployment",
];

function groupByCategory(decisions: MapDecision[]): Record<string, MapDecision[]> {
  const groups: Record<string, MapDecision[]> = {};
  for (const d of decisions) {
    const cat = d.category || "general";
    if (!groups[cat]) groups[cat] = [];
    groups[cat].push(d);
  }
  return groups;
}

function formatCategory(cat: string): string {
  return cat.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function DetailPanel({ selected, onClose, onDecisionChange, changeRecords, validationSummary, onMutate, onDelete }: DetailPanelProps) {
  const [deleting, setDeleting] = useState(false);

  // Build a map: decision_id → most recent change action for diff badges
  const changeByDecisionId = new Map<number, ChangeRecord["action"]>();
  for (const rec of changeRecords) {
    if (rec.entity_type === "decision") {
      changeByDecisionId.set(rec.entity_id, rec.action);
    }
  }

  // Build a map: decision_id → validation result for re-validation badges
  const validationByDecisionId = new Map<number, DecisionValidation>();
  if (validationSummary) {
    for (const dv of validationSummary.decision_validations) {
      if (dv.decision_id != null) {
        validationByDecisionId.set(dv.decision_id, dv);
      }
    }
  }

  // Build set of newly added file paths from Phase 0
  const newFilePaths = new Set<string>(validationSummary?.new_file_paths ?? []);

  async function handleDeleteEntity() {
    if (!selected || deleting) return;
    const name = selected.kind === "module" ? selected.module.name : selected.component.name;
    const kind = selected.kind === "module" ? "module" : "component";
    if (!window.confirm(`Delete ${kind} "${name}"? This cannot be undone.`)) return;
    setDeleting(true);
    try {
      if (selected.kind === "module") {
        await deleteModule(selected.module.id);
      } else {
        await deleteComponent(selected.component.id);
      }
      onDelete();
    } catch (e) {
      alert(`Delete failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setDeleting(false);
    }
  }

  if (!selected) return null;

  return (
        <div
          className="h-full w-96 bg-card border-l border-border shadow-xl flex flex-col shrink-0"
        >
          <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
            <h2 className="text-lg font-semibold text-foreground truncate">
              {selected.kind === "module"
                ? selected.module.name
                : selected.component.name}
            </h2>
            <div className="flex items-center gap-2">
              <Button
                variant="destructive"
                size="sm"
                className="text-xs h-7"
                onClick={handleDeleteEntity}
                disabled={deleting}
                title={`Delete this ${selected.kind}`}
              >
                {deleting ? "..." : "Delete"}
              </Button>
              <Button variant="ghost" size="sm" className="text-lg px-2 h-7" onClick={onClose}>
                &times;
              </Button>
            </div>
          </div>

          <div className="flex-1 overflow-y-auto">
            {selected.kind === "module" ? (
              <ModuleDetail
                module={selected.module}
                changeByDecisionId={changeByDecisionId}
                validationByDecisionId={validationByDecisionId}
                onDecisionChange={(d) => onDecisionChange("module", selected.module.id, d)}
                onMutate={onMutate}
              />
            ) : (
              <ComponentDetail
                component={selected.component}
                moduleName={selected.moduleName}
                changeByDecisionId={changeByDecisionId}
                validationByDecisionId={validationByDecisionId}
                newFilePaths={newFilePaths}
                onDecisionChange={(d) =>
                  onDecisionChange("component", selected.component.id, d)
                }
                onMutate={onMutate}
              />
            )}
          </div>
        </div>
  );
}

// ── Shared decisions editor ────────────────────────────────────────────────

interface DecisionsSectionProps {
  decisions: MapDecision[];
  entityId: number;
  entityType: "module" | "component";
  changeByDecisionId: Map<number, ChangeRecord["action"]>;
  validationByDecisionId: Map<number, DecisionValidation>;
  onDecisionChange: (newDecisions: MapDecision[]) => void;
  onMutate: () => void;
}

const VALIDATION_LABELS: Record<string, { label: string; cls: string }> = {
  updated:     { label: "Updated",     cls: "bg-blue-500/10 text-blue-600" },
  outdated:    { label: "Outdated",    cls: "bg-amber-500/10 text-amber-600" },
  new:         { label: "New",         cls: "bg-green-500/10 text-green-600" },
  implemented: { label: "Implemented", cls: "bg-green-500/10 text-green-600" },
  diverged:    { label: "Diverged",    cls: "bg-red-500/10 text-red-600" },
};

const DIFF_STYLES: Record<ChangeRecord["action"], { borderCls: string; badge: string; badgeCls: string }> = {
  add:    { borderCls: "border-l-green-500", badge: "+", badgeCls: "bg-green-500/10 text-green-600" },
  edit:   { borderCls: "border-l-amber-500", badge: "~", badgeCls: "bg-amber-500/10 text-amber-600" },
  remove: { borderCls: "border-l-red-500",   badge: "−", badgeCls: "bg-red-500/10 text-red-600" },
};

function DecisionsSection({
  decisions,
  entityId,
  entityType,
  changeByDecisionId,
  validationByDecisionId,
  onDecisionChange,
  onMutate,
}: DecisionsSectionProps) {
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editText, setEditText] = useState("");
  const [editDetail, setEditDetail] = useState("");
  const [editCategory, setEditCategory] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [newText, setNewText] = useState("");
  const [newDetail, setNewDetail] = useState("");
  const [newCategory, setNewCategory] = useState(DECISION_CATEGORIES[0]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const editDetailRef = useRef<HTMLTextAreaElement>(null);
  const newTextareaRef = useRef<HTMLTextAreaElement>(null);
  const newDetailRef = useRef<HTMLTextAreaElement>(null);

  function autoResize(el: HTMLTextAreaElement) {
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }

  // Focus textarea and auto-resize when editing starts
  useEffect(() => {
    if (editingId !== null && textareaRef.current) {
      autoResize(textareaRef.current);
      textareaRef.current.focus();
      textareaRef.current.select();
    }
    if (editingId !== null && editDetailRef.current) {
      autoResize(editDetailRef.current);
    }
  }, [editingId]);

  useEffect(() => {
    if (adding && newTextareaRef.current) {
      newTextareaRef.current.focus();
    }
  }, [adding]);

  function startEdit(d: MapDecision) {
    setEditingId(d.id);
    setEditText(d.text);
    setEditDetail(d.detail ?? "");
    setEditCategory(d.category);
  }

  function cancelEdit() {
    setEditingId(null);
    setEditText("");
    setEditDetail("");
    setEditCategory("");
  }

  async function saveEdit(d: MapDecision) {
    if (!editText.trim() || saving) return;
    setSaving(true);
    setSaveError(null);
    try {
      const detailValue = editDetail.trim() || null;
      await updateDecision(d.id, { text: editText.trim(), category: editCategory, detail: detailValue });
      const updated = decisions.map((x) =>
        x.id === d.id
          ? { ...x, text: editText.trim(), detail: detailValue, category: editCategory, source: "human" }
          : x,
      );
      onDecisionChange(updated);
      setEditingId(null);
      onMutate();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: number) {
    if (saving) return;
    setSaving(true);
    setSaveError(null);
    try {
      await deleteDecision(id);
      onDecisionChange(decisions.filter((x) => x.id !== id));
      onMutate();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "Delete failed");
    } finally {
      setSaving(false);
    }
  }

  async function handleAdd() {
    if (!newText.trim() || saving) return;
    setSaving(true);
    setSaveError(null);
    try {
      const detailValue = newDetail.trim() || null;
      const body =
        entityType === "module"
          ? { text: newText.trim(), category: newCategory, module_id: entityId, detail: detailValue }
          : { text: newText.trim(), category: newCategory, component_id: entityId, detail: detailValue };
      const { id } = await createDecision(body);
      const newDecision: MapDecision = {
        id,
        text: newText.trim(),
        detail: detailValue,
        category: newCategory,
        source: "human",
      };
      onDecisionChange([...decisions, newDecision]);
      setAdding(false);
      setNewText("");
      setNewDetail("");
      setNewCategory(DECISION_CATEGORIES[0]);
      onMutate();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "Add failed");
    } finally {
      setSaving(false);
    }
  }

  const groups = groupByCategory(decisions);

  return (
    <div className="px-4 py-3 border-b border-border">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-semibold text-foreground">Decisions</h3>
        <button
          className="w-6 h-6 rounded flex items-center justify-center text-sm text-primary hover:bg-muted transition-colors"
          onClick={() => setAdding((v) => !v)}
          title="Add decision"
        >
          +
        </button>
      </div>

      {Object.entries(groups).map(([cat, items]) => (
        <div key={cat} className="mb-3">
          <h4 className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">{formatCategory(cat)}</h4>
          {items.map((d) => {
            const diffAction = changeByDecisionId.get(d.id);
            const diffStyle = diffAction ? DIFF_STYLES[diffAction] : null;
            const validation = validationByDecisionId.get(d.id);
            const valInfo = validation ? VALIDATION_LABELS[validation.status] : null;
            return (
              <div
                key={d.id}
                className={cn(
                  "border-l-2 border-transparent pl-3 py-1.5 mb-1",
                  diffStyle && `border-l-4 ${diffStyle.borderCls}`,
                  valInfo && "bg-muted/30",
                )}
              >
                {editingId === d.id ? (
                  <div className="space-y-2">
                    <select
                      className="flex h-8 w-full rounded-md border border-input bg-transparent px-2 text-xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                      value={editCategory}
                      onChange={(e) => setEditCategory(e.target.value)}
                    >
                      {DECISION_CATEGORIES.map((c) => (
                        <option key={c} value={c}>
                          {formatCategory(c)}
                        </option>
                      ))}
                    </select>
                    <textarea
                      ref={textareaRef}
                      className="flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring resize-none overflow-hidden"
                      placeholder="Decision summary (short label)"
                      value={editText}
                      onChange={(e) => { setEditText(e.target.value); autoResize(e.target); }}
                      onKeyDown={(e) => {
                        if (e.key === "Escape") cancelEdit();
                        if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) saveEdit(d);
                      }}
                      rows={1}
                    />
                    <textarea
                      ref={editDetailRef}
                      className="flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring resize-none overflow-hidden"
                      placeholder="Detail (optional — deeper context)"
                      value={editDetail}
                      onChange={(e) => { setEditDetail(e.target.value); autoResize(e.target); }}
                      onKeyDown={(e) => {
                        if (e.key === "Escape") cancelEdit();
                        if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) saveEdit(d);
                      }}
                      rows={1}
                    />
                    <div className="flex gap-2">
                      <Button
                        size="sm"
                        className="text-xs h-7"
                        onClick={() => saveEdit(d)}
                        disabled={saving}
                      >
                        Save
                      </Button>
                      <Button variant="outline" size="sm" className="text-xs h-7" onClick={cancelEdit}>
                        Cancel
                      </Button>
                    </div>
                  </div>
                ) : (
                  <div>
                    {validation?.status === "updated" && validation.old_text && (
                      <span className="text-xs text-muted-foreground line-through block mb-0.5">{validation.old_text}</span>
                    )}
                    <div
                      className="flex items-start gap-2 cursor-pointer group"
                      onClick={() => {
                        if (d.detail) {
                          setExpandedIds((prev) => {
                            const next = new Set(prev);
                            if (next.has(d.id)) next.delete(d.id);
                            else next.add(d.id);
                            return next;
                          });
                        }
                      }}
                    >
                      {d.detail ? (
                        <span className="shrink-0 w-5 h-5 rounded flex items-center justify-center text-xs text-muted-foreground group-hover:text-foreground group-hover:bg-muted/60 transition-colors mt-0.5">
                          {expandedIds.has(d.id) ? "▾" : "▸"}
                        </span>
                      ) : (
                        <span className="shrink-0 w-5" />
                      )}
                      <span className="text-[13px] font-medium text-foreground leading-snug">{d.text}</span>
                    </div>
                    {d.detail && expandedIds.has(d.id) && (
                      <ul className="text-sm text-black dark:text-white mt-2 p-3 bg-muted/40 rounded-md leading-relaxed space-y-2 list-none">
                        {d.detail.split("\n").filter(Boolean).map((line, i) => (
                          <li key={i}>{line}</li>
                        ))}
                      </ul>
                    )}
                    <div className="flex items-center gap-2 mt-1 ml-5.5">
                      {valInfo && (
                        <span
                          className={cn("text-[10px] px-1.5 py-0.5 rounded font-medium", valInfo.cls)}
                          title={validation?.reason ?? undefined}
                        >
                          {valInfo.label}
                        </span>
                      )}
                      {diffStyle && (
                        <span className={cn("text-[10px] px-1.5 py-0.5 rounded font-bold", diffStyle.badgeCls)}>
                          {diffStyle.badge}
                        </span>
                      )}
                      <button
                        className="text-[10px] text-muted-foreground hover:text-primary ml-auto"
                        onClick={() => startEdit(d)}
                        title="Edit decision"
                      >
                        edit
                      </button>
                      <button
                        className="text-[10px] text-destructive hover:text-destructive/80"
                        onClick={() => handleDelete(d.id)}
                        title="Delete decision"
                      >
                        &times;
                      </button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ))}

      {decisions.length === 0 && !adding && (
        <p className="text-sm text-muted-foreground italic">No decisions yet. Click + to add one.</p>
      )}

      {saveError && (
        <p className="text-sm text-destructive mt-1">{saveError}</p>
      )}

      {adding && (
        <div className="space-y-2 mt-2 border-l-4 border-primary pl-3 py-2">
          <select
            className="flex h-8 w-full rounded-md border border-input bg-transparent px-2 text-xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            value={newCategory}
            onChange={(e) => setNewCategory(e.target.value)}
          >
            {DECISION_CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {formatCategory(c)}
              </option>
            ))}
          </select>
          <textarea
            ref={newTextareaRef}
            className="flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring resize-none overflow-hidden"
            placeholder="Decision summary (short label)"
            value={newText}
            onChange={(e) => { setNewText(e.target.value); autoResize(e.target); }}
            onKeyDown={(e) => {
              if (e.key === "Escape") setAdding(false);
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleAdd();
            }}
            rows={1}
          />
          <textarea
            ref={newDetailRef}
            className="flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring resize-none overflow-hidden"
            placeholder="Detail (optional — deeper context)"
            value={newDetail}
            onChange={(e) => { setNewDetail(e.target.value); autoResize(e.target); }}
            onKeyDown={(e) => {
              if (e.key === "Escape") setAdding(false);
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleAdd();
            }}
            rows={1}
          />
          <div className="flex gap-2">
            <Button
              size="sm"
              className="text-xs h-7"
              onClick={handleAdd}
              disabled={saving || !newText.trim()}
            >
              Add
            </Button>
            <Button variant="outline" size="sm" className="text-xs h-7" onClick={() => setAdding(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Module detail ──────────────────────────────────────────────────────────

function ModuleDetail({
  module,
  changeByDecisionId,
  validationByDecisionId,
  onDecisionChange,
  onMutate,
}: {
  module: MapModule;
  changeByDecisionId: Map<number, ChangeRecord["action"]>;
  validationByDecisionId: Map<number, DecisionValidation>;
  onDecisionChange: (newDecisions: MapDecision[]) => void;
  onMutate: () => void;
}) {
  return (
    <>
      <div className="px-4 py-3 border-b border-border space-y-1.5">
        <div className="flex items-center justify-between">
          <span className="text-xs text-muted-foreground">Type</span>
          <span className="text-sm text-foreground">{module.type}</span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-xs text-muted-foreground">Classification</span>
          <span className="text-sm text-foreground">{module.classification}</span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-xs text-muted-foreground">Technology</span>
          <span className="text-sm text-foreground">{module.technology}</span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-xs text-muted-foreground">Deployment</span>
          <span className="text-sm text-foreground">{module.deployment_target}</span>
        </div>
      </div>

      <DecisionsSection
        decisions={module.decisions}
        entityId={module.id}
        entityType="module"
        changeByDecisionId={changeByDecisionId}
        validationByDecisionId={validationByDecisionId}
        onDecisionChange={onDecisionChange}
        onMutate={onMutate}
      />

      {module.components.length > 0 && (
        <div className="px-4 py-3 border-b border-border">
          <h3 className="text-sm font-semibold text-foreground mb-2">
            Components ({module.components.length})
          </h3>
          <ul className="space-y-0.5">
            {module.components.map((c) => (
              <li key={c.id} className="text-sm text-foreground">
                {c.name}
              </li>
            ))}
          </ul>
        </div>
      )}

      {module.directories.length > 0 && (
        <div className="px-4 py-3 border-b border-border">
          <h3 className="text-sm font-semibold text-foreground mb-2">Directories</h3>
          <ul className="space-y-0.5">
            {module.directories.map((d) => (
              <li key={d} className="text-xs font-mono text-muted-foreground">
                {d}
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}

// ── Component detail ───────────────────────────────────────────────────────

function ComponentDetail({
  component,
  moduleName,
  changeByDecisionId,
  validationByDecisionId,
  newFilePaths,
  onDecisionChange,
  onMutate,
}: {
  component: MapComponent;
  moduleName: string;
  changeByDecisionId: Map<number, ChangeRecord["action"]>;
  validationByDecisionId: Map<number, DecisionValidation>;
  newFilePaths: Set<string>;
  onDecisionChange: (newDecisions: MapDecision[]) => void;
  onMutate: () => void;
}) {
  return (
    <>
      <div className="px-4 py-3 border-b border-border space-y-1.5">
        <div className="flex items-center justify-between">
          <span className="text-xs text-muted-foreground">Module</span>
          <span className="text-sm text-foreground">{moduleName}</span>
        </div>
        {component.purpose && (
          <div className="flex items-center justify-between">
            <span className="text-xs text-muted-foreground">Purpose</span>
            <span className="text-sm text-foreground">{component.purpose}</span>
          </div>
        )}
        <div className="flex items-center justify-between">
          <span className="text-xs text-muted-foreground">Confidence</span>
          <span className="text-sm text-foreground">{component.confidence}</span>
        </div>
      </div>

      <DecisionsSection
        decisions={component.decisions}
        entityId={component.id}
        entityType="component"
        changeByDecisionId={changeByDecisionId}
        validationByDecisionId={validationByDecisionId}
        onDecisionChange={onDecisionChange}
        onMutate={onMutate}
      />

      {component.files.length > 0 && (
        <div className="px-4 py-3 border-b border-border">
          <h3 className="text-sm font-semibold text-foreground mb-2">
            Files ({component.files.length})
          </h3>
          <ul className="space-y-0.5">
            {component.files.map((f) => {
              const isNew = newFilePaths.has(f.path);
              return (
                <li
                  key={f.path}
                  className={cn(
                    "text-xs font-mono text-muted-foreground flex items-center gap-2",
                    f.is_test && "text-muted-foreground/60",
                    isNew && "text-green-600",
                  )}
                >
                  {f.path}
                  {f.is_test && (
                    <span className="text-[10px] px-1 py-0.5 rounded bg-muted text-muted-foreground">test</span>
                  )}
                  {isNew && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-500/10 text-green-600 font-medium">New</span>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </>
  );
}
