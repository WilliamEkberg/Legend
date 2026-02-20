// Doc: Natural_Language_Code/Frontend/info_frontend.md

import { useState } from "react";
import { createModule, createComponent } from "../../api/client";
import type { ViewLevel, MapModule } from "../../data/types";

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
    <div className="map-modal-overlay" onClick={onClose}>
      <div className="map-modal" onClick={(e) => e.stopPropagation()}>
        <h3 className="map-modal-title">
          {level === "L2" ? "New Module" : "New Component"}
        </h3>

        <div className="map-modal-field">
          <label className="map-modal-label">Name *</label>
          <input
            className="map-modal-input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={level === "L2" ? "e.g. Auth Service" : "e.g. TokenValidator"}
            autoFocus
          />
        </div>

        {level === "L2" ? (
          <>
            <div className="map-modal-field">
              <label className="map-modal-label">Classification</label>
              <select
                className="map-modal-select"
                value={classification}
                onChange={(e) => setClassification(e.target.value)}
              >
                <option value="module">module</option>
                <option value="shared-library">shared-library</option>
                <option value="supporting-asset">supporting-asset</option>
              </select>
            </div>
            <div className="map-modal-field">
              <label className="map-modal-label">Type</label>
              <input
                className="map-modal-input"
                value={type}
                onChange={(e) => setType(e.target.value)}
                placeholder="e.g. service, library"
              />
            </div>
            <div className="map-modal-field">
              <label className="map-modal-label">Technology</label>
              <input
                className="map-modal-input"
                value={technology}
                onChange={(e) => setTechnology(e.target.value)}
                placeholder="e.g. Python, TypeScript"
              />
            </div>
          </>
        ) : (
          <>
            <div className="map-modal-field">
              <label className="map-modal-label">Module *</label>
              <select
                className="map-modal-select"
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
            <div className="map-modal-field">
              <label className="map-modal-label">Purpose</label>
              <input
                className="map-modal-input"
                value={purpose}
                onChange={(e) => setPurpose(e.target.value)}
                placeholder="What does this component do?"
              />
            </div>
          </>
        )}

        {error && <p className="map-modal-info" style={{ color: "var(--red)" }}>{error}</p>}

        <div className="map-modal-actions">
          <button className="map-modal-cancel" onClick={onClose}>
            Cancel
          </button>
          <button
            className="map-modal-save"
            onClick={handleSave}
            disabled={!name.trim() || saving || (level === "L3" && !moduleId)}
          >
            {saving ? "Saving..." : "Create"}
          </button>
        </div>
      </div>
    </div>
  );
}
