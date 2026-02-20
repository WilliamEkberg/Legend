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

  function triggerCls(trigger: string) {
    switch (trigger) {
      case "part3": return "version-trigger-part3";
      case "revalidation": return "version-trigger-reval";
      case "manual": return "version-trigger-manual";
      default: return "";
    }
  }

  return (
    <AnimatePresence>
      <motion.div
        className="version-panel"
        initial={{ y: "100%" }}
        animate={{ y: 0 }}
        exit={{ y: "100%" }}
        transition={{ type: "spring", damping: 40, stiffness: 200 }}
      >
        <div className="version-panel-header">
          <div className="version-panel-title-row">
            <h2 className="version-panel-title">Version History</h2>
            {view.kind !== "list" && (
              <button
                className="version-back-btn"
                onClick={() => setView({ kind: "list" })}
              >
                Back to list
              </button>
            )}
          </div>
          <div className="version-panel-actions">
            <button
              className="version-snapshot-btn"
              onClick={handleSnapshot}
              disabled={saving}
            >
              {saving ? "Saving..." : "Save Snapshot"}
            </button>
            <button className="version-panel-close" onClick={onClose}>
              &times;
            </button>
          </div>
        </div>

        <div className="version-panel-body">
          {loading && <p className="version-empty">Loading versions...</p>}

          {!loading && view.kind === "list" && (
            <>
              {versions.length === 0 ? (
                <p className="version-empty">
                  No versions yet. Run Part 3 or save a manual snapshot.
                </p>
              ) : (
                <>
                  <div className="version-compare-bar">
                    <select
                      className="version-compare-select"
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
                    <span className="version-compare-vs">vs</span>
                    <select
                      className="version-compare-select"
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
                    <button
                      className="version-compare-btn"
                      onClick={handleCompare}
                      disabled={compareA === "" || compareB === "" || compareA === compareB}
                    >
                      Compare
                    </button>
                  </div>

                  <table className="version-table">
                    <thead>
                      <tr>
                        <th>#</th>
                        <th>Trigger</th>
                        <th>Decisions</th>
                        <th>Date</th>
                        <th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {versions.map((v) => (
                        <tr key={v.id}>
                          <td className="version-num">v{v.version_number}</td>
                          <td>
                            <span className={`version-trigger ${triggerCls(v.trigger)}`}>
                              {triggerLabel(v.trigger)}
                            </span>
                          </td>
                          <td className="version-count">
                            {v.summary?.total_decisions ?? "—"}
                          </td>
                          <td className="version-date">
                            {new Date(v.created_at).toLocaleDateString()}
                          </td>
                          <td>
                            <button
                              className="version-view-btn"
                              onClick={() => handleViewDetail(v.id)}
                            >
                              View
                            </button>
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
  if (!version) return <p className="version-empty">Loading...</p>;

  // Group decisions by module
  const groups = new Map<string, VersionDecision[]>();
  for (const d of decisions) {
    const key = d.module_name || "(no module)";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(d);
  }

  return (
    <div className="version-detail">
      <div className="version-detail-header">
        <h3>Version {version.version_number}</h3>
        <span className="version-detail-meta">
          {version.trigger} — {new Date(version.created_at).toLocaleString()}
        </span>
      </div>
      {version.summary && (
        <div className="version-detail-summary">
          <span>{version.summary.modules} modules</span>
          <span>{version.summary.components} components</span>
          <span>{version.summary.total_decisions} decisions</span>
        </div>
      )}
      {Array.from(groups.entries()).map(([moduleName, decs]) => (
        <div key={moduleName} className="version-decision-group">
          <h4 className="version-group-title">{moduleName}</h4>
          {decs.map((d, i) => (
            <div key={i} className="version-decision-item">
              <span className="version-decision-category">{d.category}</span>
              {d.component_name && (
                <span className="version-decision-component">{d.component_name}</span>
              )}
              <p className="version-decision-text">{d.text}</p>
              <span className="version-decision-source">{d.source}</span>
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
  if (!comparison) return <p className="version-empty">Loading comparison...</p>;

  return (
    <div className="version-compare">
      <div className="version-compare-header">
        <h3>
          v{comparison.version_a.version_number} vs v{comparison.version_b.version_number}
        </h3>
        <span className="version-compare-stats">
          +{comparison.added.length} added / -{comparison.removed.length} removed / ~{comparison.changed.length} changed / {comparison.unchanged_count} unchanged
        </span>
      </div>

      {comparison.added.length > 0 && (
        <div className="version-diff-section">
          <h4 className="version-diff-label version-diff-added">Added ({comparison.added.length})</h4>
          {comparison.added.map((d, i) => (
            <div key={i} className="version-diff-item version-diff-item-added">
              <span className="version-diff-context">
                {d.module_name}{d.component_name ? ` / ${d.component_name}` : ""}
              </span>
              <span className="version-diff-category">{d.category}</span>
              <p className="version-diff-text">{d.text}</p>
            </div>
          ))}
        </div>
      )}

      {comparison.removed.length > 0 && (
        <div className="version-diff-section">
          <h4 className="version-diff-label version-diff-removed">Removed ({comparison.removed.length})</h4>
          {comparison.removed.map((d, i) => (
            <div key={i} className="version-diff-item version-diff-item-removed">
              <span className="version-diff-context">
                {d.module_name}{d.component_name ? ` / ${d.component_name}` : ""}
              </span>
              <span className="version-diff-category">{d.category}</span>
              <p className="version-diff-text">{d.text}</p>
            </div>
          ))}
        </div>
      )}

      {comparison.changed.length > 0 && (
        <div className="version-diff-section">
          <h4 className="version-diff-label version-diff-changed">Changed ({comparison.changed.length})</h4>
          {comparison.changed.map((d, i) => (
            <div key={i} className="version-diff-item version-diff-item-changed">
              <span className="version-diff-context">
                {d.module_name}{d.component_name ? ` / ${d.component_name}` : ""}
              </span>
              <div className="version-diff-old">
                <span className="version-diff-tag">Old:</span> {d.old.text}
              </div>
              <div className="version-diff-new">
                <span className="version-diff-tag">New:</span> {d.new.text}
              </div>
            </div>
          ))}
        </div>
      )}

      {comparison.added.length === 0 && comparison.removed.length === 0 && comparison.changed.length === 0 && (
        <p className="version-empty">No differences between these versions.</p>
      )}
    </div>
  );
}
