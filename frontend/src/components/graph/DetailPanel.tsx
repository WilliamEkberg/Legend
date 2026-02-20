// Doc: Natural_Language_Code/Frontend/info_frontend.md

import { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import type { SelectedNode, MapDecision, MapModule, MapComponent, ChangeRecord, ValidationSummary, DecisionValidation } from "../../data/types";
import { updateDecision, createDecision, deleteDecision, deleteModule, deleteComponent } from "../../api/client";

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

  return (
    <AnimatePresence>
      {selected && (
        <motion.div
          className="detail-panel"
          initial={{ x: "100%" }}
          animate={{ x: 0 }}
          exit={{ x: "100%" }}
          transition={{ type: "spring", damping: 40, stiffness: 200 }}
        >
          <div className="detail-panel-header">
            <h2 className="detail-panel-title">
              {selected.kind === "module"
                ? selected.module.name
                : selected.component.name}
            </h2>
            <div className="detail-panel-header-actions">
              <button
                className="detail-panel-delete"
                onClick={handleDeleteEntity}
                disabled={deleting}
                title={`Delete this ${selected.kind}`}
              >
                {deleting ? "…" : "Delete"}
              </button>
              <button className="detail-panel-close" onClick={onClose}>
                &times;
              </button>
            </div>
          </div>

          <div className="detail-panel-body">
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
        </motion.div>
      )}
    </AnimatePresence>
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
  updated:     { label: "Updated",     cls: "validation-badge-updated" },
  outdated:    { label: "Outdated",    cls: "validation-badge-outdated" },
  new:         { label: "New",         cls: "validation-badge-new" },
  implemented: { label: "Implemented", cls: "validation-badge-implemented" },
  diverged:    { label: "Diverged",    cls: "validation-badge-diverged" },
};

const DIFF_STYLES: Record<ChangeRecord["action"], { border: string; badge: string; cls: string }> = {
  add:    { border: "var(--green)",  badge: "+", cls: "diff-add" },
  edit:   { border: "var(--amber)",  badge: "~", cls: "diff-edit" },
  remove: { border: "var(--red)",    badge: "−", cls: "diff-remove" },
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
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editText, setEditText] = useState("");
  const [editCategory, setEditCategory] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [newText, setNewText] = useState("");
  const [newCategory, setNewCategory] = useState(DECISION_CATEGORIES[0]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const newTextareaRef = useRef<HTMLTextAreaElement>(null);

  // Focus textarea when editing starts
  useEffect(() => {
    if (editingId !== null && textareaRef.current) {
      textareaRef.current.focus();
      textareaRef.current.select();
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
    setEditCategory(d.category);
  }

  function cancelEdit() {
    setEditingId(null);
    setEditText("");
    setEditCategory("");
  }

  async function saveEdit(d: MapDecision) {
    if (!editText.trim() || saving) return;
    setSaving(true);
    setSaveError(null);
    try {
      await updateDecision(d.id, { text: editText.trim(), category: editCategory });
      const updated = decisions.map((x) =>
        x.id === d.id
          ? { ...x, text: editText.trim(), category: editCategory, source: "human" }
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
      const body =
        entityType === "module"
          ? { text: newText.trim(), category: newCategory, module_id: entityId }
          : { text: newText.trim(), category: newCategory, component_id: entityId };
      const { id } = await createDecision(body);
      const newDecision: MapDecision = {
        id,
        text: newText.trim(),
        category: newCategory,
        source: "human",
      };
      onDecisionChange([...decisions, newDecision]);
      setAdding(false);
      setNewText("");
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
    <div className="detail-section">
      <div className="detail-section-header">
        <h3 className="detail-section-title">Decisions</h3>
        <button
          className="decision-add-btn"
          onClick={() => setAdding((v) => !v)}
          title="Add decision"
        >
          +
        </button>
      </div>

      {Object.entries(groups).map(([cat, items]) => (
        <div key={cat} className="decision-group">
          <h4 className="decision-category">{formatCategory(cat)}</h4>
          {items.map((d) => {
            const diffAction = changeByDecisionId.get(d.id);
            const diffStyle = diffAction ? DIFF_STYLES[diffAction] : null;
            const validation = validationByDecisionId.get(d.id);
            const valInfo = validation ? VALIDATION_LABELS[validation.status] : null;
            return (
              <div
                key={d.id}
                className={`decision-item${diffStyle ? ` ${diffStyle.cls}` : ""}${valInfo ? ` validation-${validation!.status}` : ""}`}
                style={diffStyle ? { borderLeftColor: diffStyle.border } : undefined}
              >
                {editingId === d.id ? (
                  <div className="decision-edit-container">
                    <select
                      className="decision-edit-category"
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
                      className="decision-edit-textarea"
                      value={editText}
                      onChange={(e) => setEditText(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Escape") cancelEdit();
                        if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) saveEdit(d);
                      }}
                      rows={3}
                    />
                    <div className="decision-edit-actions">
                      <button
                        className="decision-save-btn"
                        onClick={() => saveEdit(d)}
                        disabled={saving}
                      >
                        Save
                      </button>
                      <button className="decision-cancel-btn" onClick={cancelEdit}>
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="decision-view">
                    {validation?.status === "updated" && validation.old_text && (
                      <span className="validation-old-text">{validation.old_text}</span>
                    )}
                    <span
                      className="decision-text"
                      onClick={() => startEdit(d)}
                      title="Click to edit"
                    >
                      {d.text}
                    </span>
                    <div className="decision-item-footer">
                      {valInfo && (
                        <span
                          className={`validation-badge ${valInfo.cls}`}
                          title={validation?.reason ?? undefined}
                        >
                          {valInfo.label}
                        </span>
                      )}
                      {diffStyle ? (
                        <span className={`diff-badge diff-badge-${diffAction}`}>
                          {diffStyle.badge}
                        </span>
                      ) : !valInfo ? (
                        <span className="decision-source">{d.source}</span>
                      ) : null}
                      <button
                        className="decision-delete-btn"
                        onClick={() => handleDelete(d.id)}
                        title="Delete decision"
                      >
                        ×
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
        <p className="decision-empty">No decisions yet. Click + to add one.</p>
      )}

      {saveError && (
        <p className="decision-error">{saveError}</p>
      )}

      {adding && (
        <div className="decision-add-form">
          <select
            className="decision-edit-category"
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
            className="decision-edit-textarea"
            placeholder="Describe the technical decision…"
            value={newText}
            onChange={(e) => setNewText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") setAdding(false);
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleAdd();
            }}
            rows={3}
          />
          <div className="decision-edit-actions">
            <button
              className="decision-save-btn"
              onClick={handleAdd}
              disabled={saving || !newText.trim()}
            >
              Add
            </button>
            <button className="decision-cancel-btn" onClick={() => setAdding(false)}>
              Cancel
            </button>
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
      <div className="detail-section">
        <div className="detail-row">
          <span className="detail-label">Type</span>
          <span className="detail-value">{module.type}</span>
        </div>
        <div className="detail-row">
          <span className="detail-label">Classification</span>
          <span className="detail-value">{module.classification}</span>
        </div>
        <div className="detail-row">
          <span className="detail-label">Technology</span>
          <span className="detail-value">{module.technology}</span>
        </div>
        <div className="detail-row">
          <span className="detail-label">Deployment</span>
          <span className="detail-value">{module.deployment_target}</span>
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
        <div className="detail-section">
          <h3 className="detail-section-title">
            Components ({module.components.length})
          </h3>
          <ul className="detail-list">
            {module.components.map((c) => (
              <li key={c.id} className="detail-list-item">
                {c.name}
              </li>
            ))}
          </ul>
        </div>
      )}

      {module.directories.length > 0 && (
        <div className="detail-section">
          <h3 className="detail-section-title">Directories</h3>
          <ul className="detail-list mono">
            {module.directories.map((d) => (
              <li key={d} className="detail-list-item">
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
      <div className="detail-section">
        <div className="detail-row">
          <span className="detail-label">Module</span>
          <span className="detail-value">{moduleName}</span>
        </div>
        {component.purpose && (
          <div className="detail-row">
            <span className="detail-label">Purpose</span>
            <span className="detail-value">{component.purpose}</span>
          </div>
        )}
        <div className="detail-row">
          <span className="detail-label">Confidence</span>
          <span className="detail-value">{component.confidence}</span>
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
        <div className="detail-section">
          <h3 className="detail-section-title">
            Files ({component.files.length})
          </h3>
          <ul className="detail-list mono">
            {component.files.map((f) => {
              const isNew = newFilePaths.has(f.path);
              return (
                <li
                  key={f.path}
                  className={`detail-list-item${f.is_test ? " test-file" : ""}${isNew ? " new-file" : ""}`}
                >
                  {f.path}
                  {f.is_test && <span className="test-badge">test</span>}
                  {isNew && <span className="validation-badge validation-badge-new">New</span>}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </>
  );
}
