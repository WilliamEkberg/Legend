// Doc: Natural_Language_Code/Frontend/info_frontend.md

import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { GroupNodeData } from "../../data/types";

export const GroupNode = memo(function GroupNode({ data }: NodeProps) {
  const { label, color } = data as GroupNodeData;

  return (
    <div
      className="rounded-2xl border-2 border-dashed p-4 min-w-[200px] min-h-[100px]"
      style={{
        borderColor: color,
        backgroundColor: `${color}10`,
      }}
    >
      <Handle type="target" position={Position.Top} id="top" className="!w-0 !h-0 !bg-transparent !border-none" />
      <Handle type="target" position={Position.Bottom} id="bottom" className="!w-0 !h-0 !bg-transparent !border-none" />
      <Handle type="target" position={Position.Left} id="left" className="!w-0 !h-0 !bg-transparent !border-none" />
      <Handle type="target" position={Position.Right} id="right" className="!w-0 !h-0 !bg-transparent !border-none" />
      <Handle type="source" position={Position.Top} id="top" className="!w-0 !h-0 !bg-transparent !border-none" />
      <Handle type="source" position={Position.Bottom} id="bottom" className="!w-0 !h-0 !bg-transparent !border-none" />
      <Handle type="source" position={Position.Left} id="left" className="!w-0 !h-0 !bg-transparent !border-none" />
      <Handle type="source" position={Position.Right} id="right" className="!w-0 !h-0 !bg-transparent !border-none" />

      <div className="text-sm font-semibold" style={{ color }}>
        {label}
      </div>
    </div>
  );
});
