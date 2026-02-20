// Doc: Natural_Language_Code/Frontend/info_frontend.md

import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { GroupNodeData } from "../../data/types";

export const GroupNode = memo(function GroupNode({ data }: NodeProps) {
  const { label, color } = data as GroupNodeData;

  return (
    <div className="group-node">
      {/* Handles for module-level edges between groups in L3 view */}
      <Handle type="target" position={Position.Top} id="top" className="group-handle" />
      <Handle type="target" position={Position.Bottom} id="bottom" className="group-handle" />
      <Handle type="target" position={Position.Left} id="left" className="group-handle" />
      <Handle type="target" position={Position.Right} id="right" className="group-handle" />
      <Handle type="source" position={Position.Top} id="top" className="group-handle" />
      <Handle type="source" position={Position.Bottom} id="bottom" className="group-handle" />
      <Handle type="source" position={Position.Left} id="left" className="group-handle" />
      <Handle type="source" position={Position.Right} id="right" className="group-handle" />

      <div className="group-node-label" style={{ color }}>
        {label}
      </div>
    </div>
  );
});
