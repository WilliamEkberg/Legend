// Doc: Natural_Language_Code/Frontend/info_frontend.md

import type { ViewLevel, EdgeFilters } from "../../data/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";

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
  onExportLlmContext: () => void;
  exportingLlmContext: boolean;
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
  onExportLlmContext,
  exportingLlmContext,
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
    <div className="w-56 h-full bg-card border-r border-border flex flex-col overflow-y-auto shrink-0">
      {/* Level toggle */}
      <div className="p-3">
        <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">Level</h3>
        <div className="flex gap-1">
          <Button
            variant={level === "L2" ? "default" : "outline"}
            size="sm"
            className="flex-1 text-xs"
            onClick={() => onLevelChange("L2")}
          >
            L2 Modules
          </Button>
          <Button
            variant={level === "L3" ? "default" : "outline"}
            size="sm"
            className="flex-1 text-xs"
            onClick={() => onLevelChange("L3")}
            disabled={!l3Available}
            title={l3Available ? undefined : "Run Part 2 first"}
          >
            L3 Components
          </Button>
        </div>
      </div>

      <Separator />

      {/* Search */}
      <div className="p-3">
        <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">Search</h3>
        <Input
          type="text"
          placeholder="Filter nodes..."
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
          spellCheck={false}
          className="h-8 text-xs"
        />
      </div>

      <Separator />

      {/* Edge type filters */}
      {edgeTypes.length > 0 && (
        <>
          <div className="p-3">
            <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">Edge Types</h3>
            <div className="space-y-1.5">
              {edgeTypes.map((type) => (
                <label key={type} className="flex items-center gap-2 text-xs text-foreground cursor-pointer">
                  <input
                    type="checkbox"
                    checked={filters.types.has(type)}
                    onChange={() => toggleEdgeType(type)}
                    className="rounded border-border"
                  />
                  <span>{type}</span>
                </label>
              ))}
            </div>
          </div>
          <Separator />
        </>
      )}

      {/* Min weight slider */}
      <div className="p-3">
        <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">Min Weight</h3>
        <input
          type="range"
          className="w-full accent-primary"
          min={0}
          max={weightRange.max}
          step={weightRange.max > 20 ? Math.ceil(weightRange.max / 20) : 0.5}
          value={filters.minWeight}
          onChange={(e) =>
            onFiltersChange({ ...filters, minWeight: Number(e.target.value) })
          }
        />
        <span className="text-xs text-muted-foreground">
          {filters.minWeight} / {weightRange.max}
        </span>
      </div>

      <Separator />

      {/* Actions */}
      <div className="p-3">
        <Button variant="outline" size="sm" className="w-full text-xs" onClick={onFitView}>
          Fit View
        </Button>
      </div>

      <div className="p-3 pt-0">
        <Button
          variant="outline"
          size="sm"
          className="w-full text-xs"
          onClick={onExportMap}
          disabled={exporting}
          title="Download the full architecture map as a JSON file"
        >
          {exporting ? "Exporting..." : "Export Map JSON"}
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="w-full text-xs mt-1.5"
          onClick={onExportLlmContext}
          disabled={exportingLlmContext}
          title="Export architecture map as LLM-friendly markdown files"
        >
          {exportingLlmContext ? "Exporting..." : "Export LLM Context"}
        </Button>
      </div>

      <Separator />

      {/* History */}
      <div className="p-3">
        <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">History</h3>
        <Button variant="outline" size="sm" className="w-full text-xs" onClick={onBrowseVersions}>
          Browse Versions
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="w-full text-xs mt-1.5"
          onClick={onSaveSnapshot}
          disabled={savingSnapshot}
        >
          {savingSnapshot ? "Saving..." : "Save Snapshot"}
        </Button>
      </div>

      <Separator />

      {/* Changes & Tickets */}
      <div className="p-3">
        <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2 flex items-center gap-2">
          Changes
          {changeCount > 0 && (
            <span className="inline-flex items-center justify-center min-w-[18px] h-[18px] rounded-full bg-primary text-primary-foreground text-[10px] font-bold px-1">
              {changeCount}
            </span>
          )}
        </h3>
        <Button
          size="sm"
          className="w-full text-xs"
          onClick={onGenerateTickets}
          disabled={generating || changeCount === 0}
          title={changeCount === 0 ? "No changes since last baseline" : "Generate implementation tickets from map changes"}
        >
          {generating ? "Generating..." : "Generate Tickets"}
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="w-full text-xs mt-1.5"
          onClick={onViewTickets}
          disabled={loadingTickets}
          title="View previously generated tickets"
        >
          {loadingTickets ? "Loading..." : "View Tickets"}
        </Button>
      </div>
    </div>
  );
}
