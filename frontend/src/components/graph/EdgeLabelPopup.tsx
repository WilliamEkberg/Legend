// Doc: Natural_Language_Code/Frontend/info_frontend.md

import { useState } from "react";
import type { ViewLevel } from "../../data/types";
import { MODULE_EDGE_TYPES, COMPONENT_EDGE_TYPES } from "../../data/types";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface EdgeLabelPopupProps {
  level: ViewLevel;
  sourceName: string;
  targetName: string;
  onSave: (edgeType: string, label: string) => void;
  onClose: () => void;
}

export function EdgeLabelPopup({
  level,
  sourceName,
  targetName,
  onSave,
  onClose,
}: EdgeLabelPopupProps) {
  const edgeTypes = level === "L2" ? MODULE_EDGE_TYPES : COMPONENT_EDGE_TYPES;
  const [edgeType, setEdgeType] = useState<string>(edgeTypes[0]);
  const [label, setLabel] = useState("");

  const handleSave = () => {
    onSave(edgeType, label.trim());
  };

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>New Edge</DialogTitle>
        </DialogHeader>

        <p className="text-sm text-muted-foreground">
          {sourceName} &rarr; {targetName}
        </p>

        <div className="space-y-4">
          <div className="space-y-2">
            <Label>Edge Type</Label>
            <select
              className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              value={edgeType}
              onChange={(e) => setEdgeType(e.target.value)}
            >
              {edgeTypes.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>

          <div className="space-y-2">
            <Label>Label (optional)</Label>
            <Input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="e.g. REST API, shared config"
              autoFocus
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={handleSave}>
            Create Edge
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
