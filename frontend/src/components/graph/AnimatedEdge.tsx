// Doc: Natural_Language_Code/Frontend/info_frontend.md

import { memo } from "react";
import {
  getBezierPath,
  type EdgeProps,
} from "@xyflow/react";
import type { MapEdgeData } from "../../data/types";

// Edge type color mapping
const EDGE_TYPE_COLORS: Record<string, { normal: string; highlighted: string }> = {
  call: {
    normal: "var(--cyan)",
    highlighted: "var(--green)",
  },
  import: {
    normal: "var(--purple)",
    highlighted: "var(--green)",
  },
  inheritance: {
    normal: "var(--green)",
    highlighted: "var(--green)",
  },
  "depends-on": {
    normal: "var(--cyan)",
    highlighted: "var(--green)",
  },
};

export const AnimatedEdge = memo(function AnimatedEdge(props: EdgeProps) {
  const {
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    data,
  } = props;

  const edgeData = data as MapEdgeData | undefined;
  const edgeType = edgeData?.edgeType ?? "call";
  const weight = edgeData?.weight ?? 1;
  const isHighlighted = edgeData?.isHighlighted ?? false;
  const isDimmed = edgeData?.isDimmed ?? false;
  const isModuleEdge = edgeData?.isModuleEdge ?? false;
  const label = edgeData?.label;

  // Smooth bezier curve — works naturally with force-directed layouts
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
  });

  // Get colors for this edge type
  const colors = EDGE_TYPE_COLORS[edgeType] ?? EDGE_TYPE_COLORS.call;
  const edgeColor = isHighlighted ? colors.highlighted : colors.normal;

  // Module edges in L3: dramatically thicker. Component edges: normal range.
  const strokeWidth = isModuleEdge
    ? Math.min(4 + Math.log2(weight) * 0.8, 8)
    : Math.min(1 + Math.log2(weight) * 0.25, 3);

  // Label sizing — module edges get ~4x larger labels
  const fontSize = isModuleEdge ? 18 : 8;
  const charW = isModuleEdge ? 11 : 5;
  const padX = isModuleEdge ? 14 : 6;
  const padY = isModuleEdge ? 13 : 6;
  const borderW = isModuleEdge ? 1 : 0.5;
  const borderR = isModuleEdge ? 4 : 2;

  // State-based opacity
  const opacity = isDimmed ? 0.3 : 1.0;

  return (
    <g className="react-flow__edge">
      {/* Main edge path */}
      <path
        d={edgePath}
        fill="none"
        stroke={edgeColor}
        strokeWidth={strokeWidth}
        opacity={opacity}
        style={{ transition: "opacity 0.15s, stroke 0.15s" }}
      />

      {/* Edge label — colored chip that reads as part of the edge, not a node */}
      {label && !isDimmed && (
        <g transform={`translate(${labelX}, ${labelY})`}>
          {/* Tinted background using the edge color at low opacity */}
          <rect
            x={-(label.length * charW) / 2 - padX}
            y={-padY}
            width={label.length * charW + padX * 2}
            height={padY * 2}
            fill="var(--bg, #0d1117)"
            fillOpacity={0.9}
            stroke={edgeColor}
            strokeWidth={borderW}
            rx={borderR}
          />
          {/* Text in the edge color so it's clearly tied to the line */}
          <text
            textAnchor="middle"
            dominantBaseline="central"
            fill={edgeColor}
            fontWeight="600"
            style={{ fontSize, letterSpacing: "0.02em" }}
          >
            {label}
          </text>
        </g>
      )}

    </g>
  );
});
