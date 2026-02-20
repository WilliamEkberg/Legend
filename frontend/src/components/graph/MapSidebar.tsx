// Doc: Natural_Language_Code/Frontend/info_frontend.md

import type { ViewLevel, EdgeFilters } from "../../data/types";

interface MapSidebarProps {
  level: ViewLevel;
  onLevelChange: (level: ViewLevel) => void;
  l3Available: boolean;
  edgeTypes: string[];
  filters: EdgeFilters;
  onFiltersChange: (filters: EdgeFilters) => void;
  searchQuery: string;
  onSearchChange: (query: string) => void;
  onFitView: () => void;
  changeCount: number;
  onGenerateTickets: () => void;
  generating: boolean;
  onViewTickets: () => void;
  loadingTickets: boolean;
  onExportMap: () => void;
  exporting: boolean;
  weightRange: { min: number; max: number };
  onBrowseVersions: () => void;
  onSaveSnapshot: () => void;
  savingSnapshot: boolean;
}

export function MapSidebar({
  level,
  onLevelChange,
  l3Available,
  edgeTypes,
  filters,
  onFiltersChange,
  searchQuery,
  onSearchChange,
  onFitView,
  changeCount,
  onGenerateTickets,
  generating,
  onViewTickets,
  loadingTickets,
  onExportMap,
  exporting,
  weightRange,
  onBrowseVersions,
  onSaveSnapshot,
  savingSnapshot,
}: MapSidebarProps) {
  function toggleEdgeType(type: string) {
    const next = new Set(filters.types);
    if (next.has(type)) {
      next.delete(type);
    } else {
      next.add(type);
    }
    onFiltersChange({ ...filters, types: next });
  }

  return (
    <div className="map-sidebar">
      <div className="sidebar-section">
        <h3 className="sidebar-title">Level</h3>
        <div className="sidebar-toggle">
          <button
            className={`toggle-btn ${level === "L2" ? "active" : ""}`}
            onClick={() => onLevelChange("L2")}
          >
            L2 Modules
          </button>
          <button
            className={`toggle-btn ${level === "L3" ? "active" : ""}`}
            onClick={() => onLevelChange("L3")}
            disabled={!l3Available}
            title={l3Available ? undefined : "Run Part 2 first"}
          >
            L3 Components
          </button>
        </div>
      </div>

      <div className="sidebar-section">
        <h3 className="sidebar-title">Search</h3>
        <input
          type="text"
          className="sidebar-search"
          placeholder="Filter nodes..."
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
          spellCheck={false}
        />
      </div>

      {edgeTypes.length > 0 && (
        <div className="sidebar-section">
          <h3 className="sidebar-title">Edge Types</h3>
          <div className="sidebar-checkboxes">
            {edgeTypes.map((type) => (
              <label key={type} className="sidebar-checkbox">
                <input
                  type="checkbox"
                  checked={filters.types.has(type)}
                  onChange={() => toggleEdgeType(type)}
                />
                <span>{type}</span>
              </label>
            ))}
          </div>
        </div>
      )}

      <div className="sidebar-section">
        <h3 className="sidebar-title">Min Weight</h3>
        <input
          type="range"
          className="sidebar-slider"
          min={0}
          max={weightRange.max}
          step={weightRange.max > 20 ? Math.ceil(weightRange.max / 20) : 0.5}
          value={filters.minWeight}
          onChange={(e) =>
            onFiltersChange({ ...filters, minWeight: Number(e.target.value) })
          }
        />
        <span className="slider-value">
          {filters.minWeight} / {weightRange.max}
        </span>
      </div>

      <div className="sidebar-section">
        <button
          className="sidebar-btn"
          onClick={onFitView}
        >
          Fit View
        </button>
      </div>

      <div className="sidebar-section">
        <button
          className="sidebar-btn sidebar-btn-export"
          onClick={onExportMap}
          disabled={exporting}
          title="Download the full architecture map as a JSON file"
        >
          {exporting ? "Exporting…" : "Export Map JSON"}
        </button>
      </div>

      <div className="sidebar-section">
        <h3 className="sidebar-title">History</h3>
        <button
          className="sidebar-btn sidebar-btn-history"
          onClick={onBrowseVersions}
        >
          Browse Versions
        </button>
        <button
          className="sidebar-btn sidebar-btn-snapshot"
          onClick={onSaveSnapshot}
          disabled={savingSnapshot}
          style={{ marginTop: 6 }}
        >
          {savingSnapshot ? "Saving..." : "Save Snapshot"}
        </button>
      </div>

      <div className="sidebar-section sidebar-tickets">
        <h3 className="sidebar-title">
          Changes
          {changeCount > 0 && (
            <span className="change-count-badge">{changeCount}</span>
          )}
        </h3>
        <button
          className={`sidebar-btn sidebar-btn-tickets${generating ? " generating" : ""}`}
          onClick={onGenerateTickets}
          disabled={generating || changeCount === 0}
          title={changeCount === 0 ? "No changes since last baseline" : "Generate implementation tickets from map changes"}
        >
          {generating ? "Generating…" : "Generate Tickets"}
        </button>
        <button
          className="sidebar-btn sidebar-btn-view-tickets"
          onClick={onViewTickets}
          disabled={loadingTickets}
          title="View previously generated tickets"
          style={{ marginTop: 6 }}
        >
          {loadingTickets ? "Loading…" : "View Tickets"}
        </button>
      </div>
    </div>
  );
}
