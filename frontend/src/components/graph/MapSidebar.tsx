// Doc: Natural_Language_Code/Frontend/info_frontend.md

import type { ViewLevel } from "../../data/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";

interface MapSidebarProps {
  level: ViewLevel;
  onLevelChange: (level: ViewLevel) => void;
  l3Available: boolean;
  searchQuery: string;
  onSearchChange: (query: string) => void;
  onFitView: () => void;
  changeCount: number;
  onGenerateTickets: () => void;
  generating: boolean;
  onViewTickets: () => void;
  loadingTickets: boolean;
  onBrowseVersions: () => void;
  onSaveSnapshot: () => void;
  savingSnapshot: boolean;
}

export function MapSidebar({
  level,
  onLevelChange,
  l3Available,
  searchQuery,
  onSearchChange,
  onFitView,
  changeCount,
  onGenerateTickets,
  generating,
  onViewTickets,
  loadingTickets,
  onBrowseVersions,
  onSaveSnapshot,
  savingSnapshot,
}: MapSidebarProps) {
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

      {/* Actions */}
      <div className="p-3">
        <Button variant="outline" size="sm" className="w-full text-xs" onClick={onFitView}>
          Fit View
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
