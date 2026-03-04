// Doc: Natural_Language_Code/revalidation/info_revalidation.md
// Doc: Natural_Language_Code/Frontend/info_frontend.md

import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import type {
  MapVersion,
  VersionDecision,
  VersionComparison,
} from "../../data/types";
import {
  fetchVersions,
  fetchVersion,
  compareVersions,
  createManualVersion,
} from "../../api/client";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface VersionPanelProps {
  onClose: () => void;
}

type PanelView =
  | { kind: "list" }
  | { kind: "detail"; versionId: number }
  | { kind: "compare"; aId: number; bId: number };

export function VersionPanel({ onClose }: VersionPanelProps) {
  const [versions, setVersions] = useState<MapVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<PanelView>({ kind: "list" });
  const [saving, setSaving] = useState(false);

  // Detail view state
  const [detailVersion, setDetailVersion] = useState<MapVersion | null>(null);
  const [detailDecisions, setDetailDecisions] = useState<VersionDecision[]>([]);

  // Compare view state
  const [comparison, setComparison] = useState<VersionComparison | null>(null);
  const [compareA, setCompareA] = useState<number | "">("");
  const [compareB, setCompareB] = useState<number | "">("");

  useEffect(() => {
    fetchVersions()
      .then((data) => setVersions(data.versions))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  function handleViewDetail(id: number) {
    setView({ kind: "detail", versionId: id });
    setDetailVersion(null);
    setDetailDecisions([]);
    fetchVersion(id)
      .then((data) => {
        setDetailVersion(data.version);
        setDetailDecisions(data.decisions);
      })
      .catch(() => {});
  }

  function handleCompare() {
    if (compareA === "" || compareB === "" || compareA === compareB) return;
    setView({ kind: "compare", aId: compareA, bId: compareB });
    setComparison(null);
    compareVersions(compareA, compareB)
      .then(setComparison)
      .catch(() => {});
  }

  async function handleSnapshot() {
    if (saving) return;
    setSaving(true);
    try {
      await createManualVersion();
      const data = await fetchVersions();
      setVersions(data.versions);
    } catch (e) {
      alert(`Snapshot failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSaving(false);
    }
  }

  function triggerLabel(trigger: string) {
    switch (trigger) {
      case "part3": return "Part 3";
      case "revalidation": return "Re-validation";
      case "manual": return "Manual";
      default: return trigger;
    }
  }

  const triggerColor: Record<string, string> = {
    part3: "bg-blue-500/10 text-blue-600",
    revalidation: "bg-amber-500/10 text-amber-600",
    manual: "bg-green-500/10 text-green-600",
  };

  return (
    <AnimatePresence>
      <motion.div
        className="absolute inset-x-0 bottom-0 max-h-[60%] bg-card border-t border-border shadow-xl z-20 flex flex-col rounded-t-xl"
        initial={{ y: "100%" }}
        animate={{ y: 0 }}
        exit={{ y: "100%" }}
        transition={{ type: "spring", damping: 40, stiffness: 200 }}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
          <div className="flex items-center gap-3">
            <h2 className="text-lg font-semibold text-foreground">Version History</h2>
            {view.kind !== "list" && (
              <Button
                variant="ghost"
                size="sm"
                className="text-xs"
                onClick={() => setView({ kind: "list" })}
              >
                Back to list
              </Button>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              className="text-xs"
              onClick={handleSnapshot}
              disabled={saving}
            >
              {saving ? "Saving..." : "Save Snapshot"}
            </Button>
            <Button variant="ghost" size="sm" className="text-lg px-2" onClick={onClose}>
              &times;
            </Button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {loading && <p className="text-sm text-muted-foreground italic">Loading versions...</p>}

          {!loading && view.kind === "list" && (
            <>
              {versions.length === 0 ? (
                <p className="text-sm text-muted-foreground italic">
                  No versions yet. Run Part 3 or save a manual snapshot.
                </p>
              ) : (
                <>
                  <div className="flex items-center gap-2 mb-4">
                    <select
                      className="flex h-8 flex-1 rounded-md border border-input bg-transparent px-2 text-xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                      value={compareA}
                      onChange={(e) => setCompareA(e.target.value ? Number(e.target.value) : "")}
                    >
                      <option value="">Version A...</option>
                      {versions.map((v) => (
                        <option key={v.id} value={v.id}>
                          v{v.version_number} — {triggerLabel(v.trigger)}
                        </option>
                      ))}
                    </select>
                    <span className="text-xs text-muted-foreground">vs</span>
                    <select
                      className="flex h-8 flex-1 rounded-md border border-input bg-transparent px-2 text-xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                      value={compareB}
                      onChange={(e) => setCompareB(e.target.value ? Number(e.target.value) : "")}
                    >
                      <option value="">Version B...</option>
                      {versions.map((v) => (
                        <option key={v.id} value={v.id}>
                          v{v.version_number} — {triggerLabel(v.trigger)}
                        </option>
                      ))}
                    </select>
                    <Button
                      size="sm"
                      className="text-xs"
                      onClick={handleCompare}
                      disabled={compareA === "" || compareB === "" || compareA === compareB}
                    >
                      Compare
                    </Button>
                  </div>

                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-border text-left">
                        <th className="py-2 pr-3 text-xs font-medium text-muted-foreground">#</th>
                        <th className="py-2 pr-3 text-xs font-medium text-muted-foreground">Trigger</th>
                        <th className="py-2 pr-3 text-xs font-medium text-muted-foreground">Decisions</th>
                        <th className="py-2 pr-3 text-xs font-medium text-muted-foreground">Date</th>
                        <th className="py-2"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {versions.map((v) => (
                        <tr key={v.id} className="border-b border-border/50 hover:bg-muted/50">
                          <td className="py-2 pr-3 text-xs font-mono text-foreground">v{v.version_number}</td>
                          <td className="py-2 pr-3">
                            <span className={cn(
                              "text-xs px-1.5 py-0.5 rounded",
                              triggerColor[v.trigger] ?? "bg-muted text-muted-foreground"
                            )}>
                              {triggerLabel(v.trigger)}
                            </span>
                          </td>
                          <td className="py-2 pr-3 text-xs text-muted-foreground">
                            {v.summary?.total_decisions ?? "—"}
                          </td>
                          <td className="py-2 pr-3 text-xs text-muted-foreground">
                            {new Date(v.created_at).toLocaleDateString()}
                          </td>
                          <td className="py-2">
                            <Button
                              variant="ghost"
                              size="sm"
                              className="text-xs h-7"
                              onClick={() => handleViewDetail(v.id)}
                            >
                              View
                            </Button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}
            </>
          )}

          {view.kind === "detail" && (
            <VersionDetailView
              version={detailVersion}
              decisions={detailDecisions}
            />
          )}

          {view.kind === "compare" && (
            <VersionCompareView comparison={comparison} />
          )}
        </div>
      </motion.div>
    </AnimatePresence>
  );
}

// ── Version Detail ──

function VersionDetailView({
  version,
  decisions,
}: {
  version: MapVersion | null;
  decisions: VersionDecision[];
}) {
  if (!version) return <p className="text-sm text-muted-foreground italic">Loading...</p>;

  // Group decisions by module
  const groups = new Map<string, VersionDecision[]>();
  for (const d of decisions) {
    const key = d.module_name || "(no module)";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(d);
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-base font-semibold text-foreground">Version {version.version_number}</h3>
        <span className="text-xs text-muted-foreground">
          {version.trigger} — {new Date(version.created_at).toLocaleString()}
        </span>
      </div>
      {version.summary && (
        <div className="flex gap-4 text-xs text-muted-foreground">
          <span>{version.summary.modules} modules</span>
          <span>{version.summary.components} components</span>
          <span>{version.summary.total_decisions} decisions</span>
        </div>
      )}
      {Array.from(groups.entries()).map(([moduleName, decs]) => (
        <div key={moduleName} className="space-y-2">
          <h4 className="text-sm font-semibold text-foreground">{moduleName}</h4>
          {decs.map((d, i) => (
            <div key={i} className="border-l-2 border-border pl-3 py-1">
              <div className="flex items-center gap-2">
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground">{d.category}</span>
                {d.component_name && (
                  <span className="text-xs text-primary">{d.component_name}</span>
                )}
              </div>
              <p className="text-sm text-foreground mt-0.5">{d.text}</p>
              <span className="text-[10px] text-muted-foreground">{d.source}</span>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

// ── Version Compare ──

function VersionCompareView({
  comparison,
}: {
  comparison: VersionComparison | null;
}) {
  if (!comparison) return <p className="text-sm text-muted-foreground italic">Loading comparison...</p>;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-base font-semibold text-foreground">
          v{comparison.version_a.version_number} vs v{comparison.version_b.version_number}
        </h3>
        <span className="text-xs text-muted-foreground">
          +{comparison.added.length} added / -{comparison.removed.length} removed / ~{comparison.changed.length} changed / {comparison.unchanged_count} unchanged
        </span>
      </div>

      {comparison.added.length > 0 && (
        <div className="space-y-2">
          <h4 className="text-sm font-semibold text-green-600">Added ({comparison.added.length})</h4>
          {comparison.added.map((d, i) => (
            <div key={i} className="border-l-4 border-green-500 pl-3 py-2 bg-green-500/5 rounded-r">
              <span className="text-xs text-muted-foreground">
                {d.module_name}{d.component_name ? ` / ${d.component_name}` : ""}
              </span>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground ml-2">{d.category}</span>
              <p className="text-sm text-foreground mt-0.5">{d.text}</p>
            </div>
          ))}
        </div>
      )}

      {comparison.removed.length > 0 && (
        <div className="space-y-2">
          <h4 className="text-sm font-semibold text-red-600">Removed ({comparison.removed.length})</h4>
          {comparison.removed.map((d, i) => (
            <div key={i} className="border-l-4 border-red-500 pl-3 py-2 bg-red-500/5 rounded-r">
              <span className="text-xs text-muted-foreground">
                {d.module_name}{d.component_name ? ` / ${d.component_name}` : ""}
              </span>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground ml-2">{d.category}</span>
              <p className="text-sm text-foreground mt-0.5">{d.text}</p>
            </div>
          ))}
        </div>
      )}

      {comparison.changed.length > 0 && (
        <div className="space-y-2">
          <h4 className="text-sm font-semibold text-amber-600">Changed ({comparison.changed.length})</h4>
          {comparison.changed.map((d, i) => (
            <div key={i} className="border-l-4 border-amber-500 pl-3 py-2 bg-amber-500/5 rounded-r">
              <span className="text-xs text-muted-foreground">
                {d.module_name}{d.component_name ? ` / ${d.component_name}` : ""}
              </span>
              <div className="mt-1 text-sm">
                <div className="text-red-600/70 line-through">
                  <span className="text-[10px] font-medium mr-1">Old:</span> {d.old.text}
                </div>
                <div className="text-green-600">
                  <span className="text-[10px] font-medium mr-1">New:</span> {d.new.text}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {comparison.added.length === 0 && comparison.removed.length === 0 && comparison.changed.length === 0 && (
        <p className="text-sm text-muted-foreground italic">No differences between these versions.</p>
      )}
    </div>
  );
}
