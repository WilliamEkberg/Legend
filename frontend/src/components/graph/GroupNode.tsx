// Doc: Natural_Language_Code/Frontend/info_frontend.md

import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { GroupNodeData } from "../../data/types";

export const GroupNode = memo(function GroupNode({ data }: NodeProps) {
  const { label, color } = data as GroupNodeData;

  const bgColor = color.replace(")", " / 0.06)");

  return (
    <div
      className="w-full h-full relative rounded-2xl"
      style={{
        backgroundColor: bgColor,
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

      {/* Module name badge — top center on the border */}
      <div
        className="absolute left-1/2 top-0 -translate-x-1/2 -translate-y-1/2 px-4 py-1 rounded-md text-sm font-bold uppercase tracking-wide whitespace-nowrap"
        style={{
          color,
          backgroundColor: "hsl(var(--background))",
          border: `1.5px solid ${color}`,
        }}
      >
        {label}
      </div>
    </div>
  );
});
