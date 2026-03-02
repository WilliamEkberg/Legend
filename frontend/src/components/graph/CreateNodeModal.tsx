// Doc: Natural_Language_Code/Frontend/info_frontend.md

import { useState } from "react";
import { createModule, createComponent } from "../../api/client";
import type { ViewLevel, MapModule } from "../../data/types";
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

interface CreateNodeModalProps {
  level: ViewLevel;
  modules: MapModule[];
  onSave: () => void;
  onClose: () => void;
}

export function CreateNodeModal({ level, modules, onSave, onClose }: CreateNodeModalProps) {
  const [name, setName] = useState("");
  const [classification, setClassification] = useState("module");
  const [type, setType] = useState("");
  const [technology, setTechnology] = useState("");
  const [moduleId, setModuleId] = useState<number | "">(modules[0]?.id ?? "");
  const [purpose, setPurpose] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const handleSave = async () => {
    if (!name.trim()) return;
    setSaving(true);
    setError("");
    try {
      if (level === "L2") {
        await createModule({
          name: name.trim(),
          classification,
          type: type.trim() || undefined,
          technology: technology.trim() || undefined,
        });
      } else {
        if (!moduleId) return;
        await createComponent({
          module_id: moduleId as number,
          name: name.trim(),
          purpose: purpose.trim() || undefined,
        });
      }
      onSave();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create");
      setSaving(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            {level === "L2" ? "New Module" : "New Component"}
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-2">
            <Label>Name *</Label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={level === "L2" ? "e.g. Auth Service" : "e.g. TokenValidator"}
              autoFocus
            />
          </div>

          {level === "L2" ? (
            <>
              <div className="space-y-2">
                <Label>Classification</Label>
                <select
                  className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  value={classification}
                  onChange={(e) => setClassification(e.target.value)}
                >
                  <option value="module">module</option>
                  <option value="shared-library">shared-library</option>
                  <option value="supporting-asset">supporting-asset</option>
                </select>
              </div>
              <div className="space-y-2">
                <Label>Type</Label>
                <Input
                  value={type}
                  onChange={(e) => setType(e.target.value)}
                  placeholder="e.g. service, library"
                />
              </div>
              <div className="space-y-2">
                <Label>Technology</Label>
                <Input
                  value={technology}
                  onChange={(e) => setTechnology(e.target.value)}
                  placeholder="e.g. Python, TypeScript"
                />
              </div>
            </>
          ) : (
            <>
              <div className="space-y-2">
                <Label>Module *</Label>
                <select
                  className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  value={moduleId}
                  onChange={(e) => setModuleId(Number(e.target.value))}
                >
                  {modules.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name}
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-2">
                <Label>Purpose</Label>
                <Input
                  value={purpose}
                  onChange={(e) => setPurpose(e.target.value)}
                  placeholder="What does this component do?"
                />
              </div>
            </>
          )}

          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={handleSave}
            disabled={!name.trim() || saving || (level === "L3" && !moduleId)}
          >
            {saving ? "Saving..." : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
