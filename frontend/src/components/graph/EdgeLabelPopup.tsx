// Doc: Natural_Language_Code/Frontend/info_frontend.md

import { useState } from "react";
import type { ViewLevel } from "../../data/types";
import { MODULE_EDGE_TYPES, COMPONENT_EDGE_TYPES } from "../../data/types";

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
    <div className="map-modal-overlay" onClick={onClose}>
      <div className="map-modal" onClick={(e) => e.stopPropagation()}>
        <h3 className="map-modal-title">New Edge</h3>

        <p className="map-modal-info">
          {sourceName} &rarr; {targetName}
        </p>

        <div className="map-modal-field">
          <label className="map-modal-label">Edge Type</label>
          <select
            className="map-modal-select"
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

        <div className="map-modal-field">
          <label className="map-modal-label">Label (optional)</label>
          <input
            className="map-modal-input"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="e.g. REST API, shared config"
            autoFocus
          />
        </div>

        <div className="map-modal-actions">
          <button className="map-modal-cancel" onClick={onClose}>
            Cancel
          </button>
          <button className="map-modal-save" onClick={handleSave}>
            Create Edge
          </button>
        </div>
      </div>
    </div>
  );
}
