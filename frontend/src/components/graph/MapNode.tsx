// Doc: Natural_Language_Code/Frontend/info_frontend.md

import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { cn } from "@/lib/utils";
import type { ModuleNodeData, ComponentNodeData } from "../../data/types";

type MapNodeData = ModuleNodeData | ComponentNodeData;

function isModuleData(data: MapNodeData): data is ModuleNodeData {
  return "moduleType" in data;
}

const CLASSIFICATION_COLOR: Record<string, string> = {
  module: "hsl(var(--node-component))",
  "shared-library": "hsl(var(--node-actor))",
  "supporting-asset": "hsl(var(--node-utility))",
};

export const MapNode = memo(function MapNode({ data, selected }: NodeProps) {
  const nodeData = data as MapNodeData;
  const isModule = isModuleData(nodeData);
  const isExternal = isModule
    ? nodeData.directories.length === 0
    : nodeData.files.length === 0;
  const hasChanges = (data as { hasChanges?: boolean }).hasChanges === true;
  const hasRevalidation = (data as { hasRevalidation?: boolean }).hasRevalidation === true;

  const borderColor = isModule
    ? CLASSIFICATION_COLOR[nodeData.classification] ?? CLASSIFICATION_COLOR.module
    : "hsl(var(--node-component))";

  return (
    <div
      className={cn(
        "rounded-lg bg-card border border-border border-l-4 px-3 py-2 min-w-[140px] max-w-[220px] shadow-sm transition-all",
        selected && "ring-2 ring-primary shadow-md",
        hasChanges && "ring-2 ring-amber-500",
        hasRevalidation && "ring-2 ring-blue-500",
      )}
      style={{ borderLeftColor: borderColor }}
    >
      <Handle type="target" position={Position.Top} id="top" className="!w-2 !h-2 !bg-border !border-none" />
      <Handle type="target" position={Position.Bottom} id="bottom" className="!w-2 !h-2 !bg-border !border-none" />
      <Handle type="target" position={Position.Left} id="left" className="!w-2 !h-2 !bg-border !border-none" />
      <Handle type="target" position={Position.Right} id="right" className="!w-2 !h-2 !bg-border !border-none" />
      <Handle type="source" position={Position.Top} id="top" className="!w-2 !h-2 !bg-border !border-none" />
      <Handle type="source" position={Position.Bottom} id="bottom" className="!w-2 !h-2 !bg-border !border-none" />
      <Handle type="source" position={Position.Left} id="left" className="!w-2 !h-2 !bg-border !border-none" />
      <Handle type="source" position={Position.Right} id="right" className="!w-2 !h-2 !bg-border !border-none" />

      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium text-foreground truncate">{nodeData.label}</span>
        {isModule && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground whitespace-nowrap">
            {nodeData.moduleType}
          </span>
        )}
      </div>
      <div className="flex items-center justify-between gap-2 mt-1">
        {isModule ? (
          <span className="text-xs text-muted-foreground truncate">{nodeData.technology}</span>
        ) : (
          <span className="text-xs text-muted-foreground truncate">{nodeData.moduleName}</span>
        )}
        <span className="text-[10px] text-muted-foreground whitespace-nowrap">
          {isModule
            ? `${nodeData.componentCount} comp`
            : `${nodeData.fileCount} files`}
        </span>
      </div>
      {isExternal && (
        <div className="text-[10px] text-muted-foreground/70 mt-1 italic">External dependency</div>
      )}
    </div>
  );
});
