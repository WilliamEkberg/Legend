// Doc: Natural_Language_Code/Frontend/info_frontend.md

import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { ModuleNodeData, ComponentNodeData } from "../../data/types";

type MapNodeData = ModuleNodeData | ComponentNodeData;

function isModuleData(data: MapNodeData): data is ModuleNodeData {
  return "moduleType" in data;
}

export const MapNode = memo(function MapNode({ data, selected }: NodeProps) {
  const nodeData = data as MapNodeData;
  const isModule = isModuleData(nodeData);
  const isExternal = isModule
    ? nodeData.directories.length === 0
    : nodeData.files.length === 0;
  const hasChanges = (data as { hasChanges?: boolean }).hasChanges === true;
  const hasRevalidation = (data as { hasRevalidation?: boolean }).hasRevalidation === true;

  return (
    <div
      className={`map-node ${selected ? "selected" : ""} ${isModule ? "module" : "component"} ${hasChanges ? "has-changes" : ""} ${hasRevalidation ? "has-revalidation" : ""}`}
      data-classification={isModule ? nodeData.classification : undefined}
    >
      {/* Handles on all 4 sides — edges pick the best one based on node positions */}
      <Handle type="target" position={Position.Top} id="top" className="map-handle" />
      <Handle type="target" position={Position.Bottom} id="bottom" className="map-handle" />
      <Handle type="target" position={Position.Left} id="left" className="map-handle" />
      <Handle type="target" position={Position.Right} id="right" className="map-handle" />
      <Handle type="source" position={Position.Top} id="top" className="map-handle" />
      <Handle type="source" position={Position.Bottom} id="bottom" className="map-handle" />
      <Handle type="source" position={Position.Left} id="left" className="map-handle" />
      <Handle type="source" position={Position.Right} id="right" className="map-handle" />

      <div className="map-node-header">
        <span className="map-node-label">{nodeData.label}</span>
        {isModule && (
          <span
            className="map-node-badge"
            data-classification={nodeData.classification}
          >
            {nodeData.moduleType}
          </span>
        )}
      </div>
      <div className="map-node-meta">
        {isModule ? (
          <span className="map-node-tech">{nodeData.technology}</span>
        ) : (
          <span className="map-node-module">{nodeData.moduleName}</span>
        )}
        <span className="map-node-count">
          {isModule
            ? `${nodeData.componentCount} comp`
            : `${nodeData.fileCount} files`}
        </span>
      </div>
      {isExternal && (
        <div className="map-node-external">External dependency</div>
      )}
    </div>
  );
});
