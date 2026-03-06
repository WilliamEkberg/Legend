// Doc: Natural_Language_Code/Frontend/info_frontend.md

import { memo, useState } from "react";
import { Position, EdgeLabelRenderer, type EdgeProps } from "@xyflow/react";
import type { MapEdgeData } from "../../data/types";

// Highly distinct colors per edge type — easy to tell apart at a glance
const EDGE_TYPE_COLORS: Record<string, string> = {
  call:              "#00e5ff", // cyan
  import:            "#ff4da6", // magenta-pink
  inheritance:       "#4ade80", // bright green
  "depends-on":      "#ff9500", // orange
  depends_on:        "#ff9500", // orange
  uses_data_store:   "#fbbf24", // gold
  communicates_via:  "#a78bfa", // purple
};

// Hover: bright white so it pops over everything
const HOVER_COLOR = "#ffffff";

// Human-readable directional labels
const EDGE_TYPE_LABELS: Record<string, string> = {
  call: "calls",
  import: "imports",
  "depends-on": "depends on",
  depends_on: "depends on",
  inheritance: "inherits from",
  uses_data_store: "uses data store",
  communicates_via: "communicates via",
};

// ── Orthogonal path routing ──

function getOrthogonalPath(
  sx: number, sy: number, sPos: Position,
  tx: number, ty: number, tPos: Position,
): string {
  const offset = 30;

  let s1x = sx, s1y = sy;
  if (sPos === Position.Right) s1x += offset;
  else if (sPos === Position.Left) s1x -= offset;
  else if (sPos === Position.Bottom) s1y += offset;
  else s1y -= offset;

  let t1x = tx, t1y = ty;
  if (tPos === Position.Right) t1x += offset;
  else if (tPos === Position.Left) t1x -= offset;
  else if (tPos === Position.Bottom) t1y += offset;
  else t1y -= offset;

  const isSourceH = sPos === Position.Left || sPos === Position.Right;
  const isTargetH = tPos === Position.Left || tPos === Position.Right;

  const pts: [number, number][] = [[sx, sy], [s1x, s1y]];

  if (isSourceH && isTargetH) {
    const midX = (s1x + t1x) / 2;
    pts.push([midX, s1y], [midX, t1y]);
  } else if (!isSourceH && !isTargetH) {
    const midY = (s1y + t1y) / 2;
    pts.push([s1x, midY], [t1x, midY]);
  } else if (isSourceH) {
    pts.push([t1x, s1y]);
  } else {
    pts.push([s1x, t1y]);
  }

  pts.push([t1x, t1y], [tx, ty]);

  return pts.map((p, i) =>
    i === 0 ? `M ${p[0]} ${p[1]}` : `L ${p[0]} ${p[1]}`
  ).join(" ");
}

// ── Edge component ──

export const AnimatedEdge = memo(function AnimatedEdge(props: EdgeProps) {
  const {
    id,
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
  const rawLabel = edgeData?.label;
  const description = edgeData?.description;

  // Build display label
  const typeLabel = EDGE_TYPE_LABELS[edgeType] ?? edgeType;
  const detail = rawLabel || description;
  const displayLabel = detail ? `${typeLabel}: ${detail}` : typeLabel;

  // Orthogonal routing
  const edgePath = getOrthogonalPath(
    sourceX, sourceY, sourcePosition ?? Position.Bottom,
    targetX, targetY, targetPosition ?? Position.Top,
  );

  // Label at geometric midpoint
  const labelX = (sourceX + targetX) / 2;
  const labelY = (sourceY + targetY) / 2;

  // Colors — distinct per type, bright white on hover only
  const normalColor = EDGE_TYPE_COLORS[edgeType] ?? "#00e5ff";
  const [hovered, setHovered] = useState(false);
  const [pinned, setPinned] = useState(false);
  const edgeColor = hovered ? HOVER_COLOR : normalColor;

  // Stroke width
  const strokeWidth = isModuleEdge
    ? Math.min(4 + Math.log2(weight) * 0.8, 8)
    : Math.min(1 + Math.log2(weight) * 0.25, 3);

  const opacity = isDimmed ? 0.15 : 1.0;
  const showLabel = !isDimmed && (pinned || isHighlighted || hovered);

  // Arrowhead
  const markerId = `arrow-${id}`;
  const arrowW = isModuleEdge ? 10 : 7;
  const arrowH = isModuleEdge ? 8 : 5;

  // Label sizing
  const fontSize = isModuleEdge ? 13 : 10;
  const padX = isModuleEdge ? 10 : 8;
  const padY = isModuleEdge ? 6 : 5;
  const borderR = isModuleEdge ? 5 : 4;

  return (
    <g
      className="react-flow__edge"
      style={{ zIndex: hovered ? 9999 : 0 }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <defs>
        <marker
          id={markerId}
          markerWidth={arrowW}
          markerHeight={arrowH}
          refX={arrowW - 1}
          refY={arrowH / 2}
          orient="auto"
          markerUnits="userSpaceOnUse"
        >
          <polygon
            points={`0 0, ${arrowW} ${arrowH / 2}, 0 ${arrowH}`}
            fill={edgeColor}
            opacity={opacity}
          />
        </marker>
      </defs>

      {/* Invisible wider hit area + click to pin/unpin label */}
      <path
        d={edgePath}
        fill="none"
        stroke="transparent"
        strokeWidth={Math.max(strokeWidth + 16, 20)}
        strokeLinejoin="round"
        style={{ cursor: "pointer" }}
        onClick={(e) => { e.stopPropagation(); setPinned((v) => !v); }}
      />

      {/* Glow effect on hover — makes hovered edge visually pop above others */}
      {hovered && (
        <path
          d={edgePath}
          fill="none"
          stroke={HOVER_COLOR}
          strokeWidth={strokeWidth + 6}
          strokeLinejoin="round"
          opacity={0.25}
          pointerEvents="none"
        />
      )}

      {/* Main edge path with directional arrowhead */}
      <path
        d={edgePath}
        fill="none"
        stroke={edgeColor}
        strokeWidth={hovered ? strokeWidth + 1.5 : strokeWidth}
        strokeLinejoin="round"
        opacity={opacity}
        markerEnd={`url(#${markerId})`}
        pointerEvents="none"
        style={{ transition: "opacity 0.15s, stroke 0.1s, stroke-width 0.1s" }}
      />

      {/* Edge label — rendered in HTML layer so it sits on top of ALL nodes and edges */}
      {showLabel && (() => {
        const labelColor = hovered ? HOVER_COLOR : normalColor;
        return (
          <EdgeLabelRenderer>
            <div
              style={{
                position: "absolute",
                transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
                pointerEvents: pinned ? "auto" : "none",
              cursor: pinned ? "pointer" : "default",
                zIndex: 9999,
              }}
            >
              <div
                onClick={(e) => { e.stopPropagation(); setPinned(false); }}
                style={{
                  background: "hsl(var(--background))",
                  border: `1.5px solid ${labelColor}`,
                  borderRadius: borderR,
                  padding: `${padY}px ${padX}px`,
                  fontSize,
                  fontWeight: 600,
                  color: labelColor,
                  whiteSpace: "nowrap",
                  letterSpacing: "0.02em",
                  boxShadow: "0 2px 12px rgba(0,0,0,0.5)",
                }}
              >
                {displayLabel}
              </div>
            </div>
          </EdgeLabelRenderer>
        );
      })()}
    </g>
  );
});
