// Doc: Natural_Language_Code/Frontend/info_frontend.md

import type { Node, Edge } from "@xyflow/react";
import {
  forceSimulation,
  forceLink,
  forceManyBody,
  forceCenter,
  forceCollide,
  forceX,
  forceY,
  type SimulationNodeDatum,
  type SimulationLinkDatum,
} from "d3-force";
import type {
  MapData,
  ViewLevel,
  ModuleNodeData,
  ComponentNodeData,
  GroupNodeData,
  MapEdgeData,
  EdgeFilters,
} from "./types";
import { autoLayoutModules } from "./autoLayout";

// ── L3 layout constants ──
const COMPONENT_NODE_W = 240;
const COMPONENT_NODE_H = 70;
const GROUP_PADDING = 40;
const GROUP_LABEL_HEIGHT = 24;
const REPULSION_STRENGTH = -2000;

// ── Hierarchical layout constants ──
const LAYER_GAP = COMPONENT_NODE_H + 60;
const HORIZONTAL_GAP = 40;

// ── Handle selection helper ──

function assignEdgeHandles(
  edges: Edge<MapEdgeData>[],
  nodeMap: Map<string, { x: number; y: number; w: number; h: number }>
): Edge<MapEdgeData>[] {
  return edges.map((edge) => {
    const src = nodeMap.get(edge.source);
    const tgt = nodeMap.get(edge.target);
    if (!src || !tgt) return edge;

    const srcCx = src.x + src.w / 2;
    const srcCy = src.y + src.h / 2;
    const tgtCx = tgt.x + tgt.w / 2;
    const tgtCy = tgt.y + tgt.h / 2;

    const dx = tgtCx - srcCx;
    const dy = tgtCy - srcCy;

    let sourceHandle: string;
    let targetHandle: string;

    if (Math.abs(dx) > Math.abs(dy)) {
      sourceHandle = dx > 0 ? "right" : "left";
      targetHandle = dx > 0 ? "left" : "right";
    } else {
      sourceHandle = dy > 0 ? "bottom" : "top";
      targetHandle = dy > 0 ? "top" : "bottom";
    }

    return { ...edge, sourceHandle, targetHandle };
  });
}

// ── Color map by classification ──

const CLASSIFICATION_COLORS: Record<string, string> = {
  module: "#00e5ff",
  "shared-library": "#bd93f9",
  "supporting-asset": "#ffb300",
};

function classificationColor(classification: string): string {
  return CLASSIFICATION_COLORS[classification] ?? "#4a5568";
}

// ── Transform for L2 (modules) ──

async function buildL2(
  data: MapData,
  filters: EdgeFilters
): Promise<{ nodes: Node<ModuleNodeData>[]; edges: Edge<MapEdgeData>[] }> {
  const nodes = autoLayoutModules(data);

  const moduleIds = new Set(data.modules.map((m) => m.id));
  const edges: Edge<MapEdgeData>[] = data.module_edges
    .filter(
      (e) =>
        moduleIds.has(e.source_id) &&
        moduleIds.has(e.target_id) &&
        filters.types.has(e.edge_type) &&
        e.weight >= filters.minWeight
    )
    .map((e) => {
      const metadata = e.metadata || {};
      return {
        id: `edge-module-${e.source_id}-${e.target_id}-${e.edge_type}`,
        source: `module-${e.source_id}`,
        target: `module-${e.target_id}`,
        type: "animatedEdge",
        data: {
          edgeType: e.edge_type,
          weight: e.weight,
          label: metadata.label as string | undefined,
          description: (metadata.description || metadata.label) as string | undefined,
          isHighlighted: false,
          isDimmed: false,
        },
      };
    });

  const nodeMap = new Map<string, { x: number; y: number; w: number; h: number }>();
  for (const n of nodes) {
    nodeMap.set(n.id, {
      x: n.position.x,
      y: n.position.y,
      w: COMPONENT_NODE_W,
      h: COMPONENT_NODE_H,
    });
  }

  return { nodes, edges: assignEdgeHandles(edges, nodeMap) };
}

// ── L3: Per-module layout types ──

interface ModuleLayout {
  moduleId: number;
  positions: Map<number, { x: number; y: number }>;
  bbox: { w: number; h: number };
  intraEdgeCount: number;
}

interface GroupBox {
  moduleId: number;
  x: number;
  y: number;
  w: number;
  h: number;
}

interface GroupSimNode extends SimulationNodeDatum {
  id: string;
  moduleId: number;
  w: number;
  h: number;
  degree: number;
}

const GROUP_GAP = 30;

// ── Phase 1: Per-module hierarchical layout ──

/**
 * Find connected components in an undirected graph.
 */
function findConnectedComponents(
  nodeIds: number[],
  adj: Map<number, Set<number>>
): number[][] {
  const visited = new Set<number>();
  const components: number[][] = [];

  for (const start of nodeIds) {
    if (visited.has(start)) continue;
    const component: number[] = [];
    const stack = [start];
    while (stack.length > 0) {
      const node = stack.pop()!;
      if (visited.has(node)) continue;
      visited.add(node);
      component.push(node);
      const neighbors = adj.get(node);
      if (neighbors) {
        for (const n of neighbors) {
          if (!visited.has(n)) stack.push(n);
        }
      }
    }
    components.push(component);
  }
  return components;
}

/**
 * BFS layer assignment from the highest-degree node in a connected component.
 * Returns map: nodeId -> layer index (0 = core/top).
 */
function assignLayers(
  componentNodes: number[],
  adj: Map<number, Set<number>>,
  weightedDegree: Map<number, number>
): Map<number, number> {
  // Start from the node with highest weighted degree (the "core")
  let coreNode = componentNodes[0];
  let maxDeg = weightedDegree.get(coreNode) ?? 0;
  for (const id of componentNodes) {
    const deg = weightedDegree.get(id) ?? 0;
    if (deg > maxDeg) {
      maxDeg = deg;
      coreNode = id;
    }
  }

  const layers = new Map<number, number>();
  layers.set(coreNode, 0);
  const queue = [coreNode];
  let head = 0;

  while (head < queue.length) {
    const node = queue[head++];
    const currentLayer = layers.get(node)!;
    const neighbors = adj.get(node);
    if (!neighbors) continue;
    for (const n of neighbors) {
      if (!layers.has(n)) {
        layers.set(n, currentLayer + 1);
        queue.push(n);
      }
    }
  }

  return layers;
}

/**
 * Group nodes by layer index.
 */
function groupByLayer(layers: Map<number, number>): Map<number, number[]> {
  const result = new Map<number, number[]>();
  for (const [nodeId, layer] of layers) {
    const arr = result.get(layer) ?? [];
    arr.push(nodeId);
    result.set(layer, arr);
  }
  return result;
}

/**
 * Barycenter ordering: order nodes in each layer to minimize edge crossings
 * by placing each node at the average position of its neighbors in the adjacent layer.
 * Two passes: top-down then bottom-up.
 */
function orderLayers(
  layerGroups: Map<number, number[]>,
  adj: Map<number, Set<number>>
): Map<number, number[]> {
  const maxLayer = Math.max(...layerGroups.keys());

  // Build nodeId -> layer lookup for O(1) access
  const nodeToLayer = new Map<number, number>();
  for (const [layer, nodes] of layerGroups) {
    for (const id of nodes) nodeToLayer.set(id, layer);
  }

  // Build position lookup: nodeId -> index within its layer
  const posInLayer = new Map<number, number>();
  for (const [, nodes] of layerGroups) {
    nodes.forEach((id, i) => posInLayer.set(id, i));
  }

  function sortLayer(l: number, refLayer: number) {
    const nodes = layerGroups.get(l);
    if (!nodes || nodes.length <= 1) return;

    const barycenters = nodes.map((id) => {
      const neighbors = adj.get(id);
      if (!neighbors) return { id, bc: 0 };
      let sum = 0;
      let count = 0;
      for (const n of neighbors) {
        if (nodeToLayer.get(n) === refLayer) {
          sum += posInLayer.get(n) ?? 0;
          count++;
        }
      }
      return { id, bc: count > 0 ? sum / count : posInLayer.get(id) ?? 0 };
    });

    barycenters.sort((a, b) => a.bc - b.bc);
    const sorted = barycenters.map((b) => b.id);
    layerGroups.set(l, sorted);
    sorted.forEach((id, i) => posInLayer.set(id, i));
  }

  // Top-down pass: order each layer based on positions in previous layer
  for (let l = 1; l <= maxLayer; l++) sortLayer(l, l - 1);

  // Bottom-up pass: refine based on positions in next layer
  for (let l = maxLayer - 1; l >= 0; l--) sortLayer(l, l + 1);

  return layerGroups;
}

function layoutModule(
  mod: { id: number; components: { id: number; name: string; purpose: string; confidence: number; files: { path: string; is_test: boolean }[]; decisions: { id: number; category: string; text: string; source: string }[] }[] },
  intraEdges: { source_id: number; target_id: number; weight?: number }[],
): ModuleLayout {
  const n = mod.components.length;
  const compIds = mod.components.map((c) => c.id);

  // Single-component module: trivial layout
  if (n === 1) {
    return {
      moduleId: mod.id,
      positions: new Map([[compIds[0], { x: 0, y: 0 }]]),
      bbox: { w: COMPONENT_NODE_W, h: COMPONENT_NODE_H },
      intraEdgeCount: 0,
    };
  }

  // Build adjacency and weighted degree
  const nodeSet = new Set(compIds);
  const adj = new Map<number, Set<number>>();
  const weightedDegree = new Map<number, number>();
  for (const id of compIds) {
    adj.set(id, new Set());
    weightedDegree.set(id, 0);
  }

  const seenEdges = new Set<string>();
  let edgeCount = 0;
  for (const e of intraEdges) {
    if (!nodeSet.has(e.source_id) || !nodeSet.has(e.target_id)) continue;
    const key = `${Math.min(e.source_id, e.target_id)}-${Math.max(e.source_id, e.target_id)}`;
    if (seenEdges.has(key)) continue;
    seenEdges.add(key);
    edgeCount++;
    adj.get(e.source_id)!.add(e.target_id);
    adj.get(e.target_id)!.add(e.source_id);
    const w = e.weight ?? 1;
    weightedDegree.set(e.source_id, (weightedDegree.get(e.source_id) ?? 0) + w);
    weightedDegree.set(e.target_id, (weightedDegree.get(e.target_id) ?? 0) + w);
  }

  // Separate connected and disconnected components
  const connectedNodes = new Set<number>();
  for (const id of compIds) {
    if ((adj.get(id)?.size ?? 0) > 0) connectedNodes.add(id);
  }
  const disconnected = compIds.filter((id) => !connectedNodes.has(id));
  const connected = compIds.filter((id) => connectedNodes.has(id));

  // All disconnected: grid layout
  if (connected.length === 0) {
    const positions = new Map<number, { x: number; y: number }>();
    const cols = Math.ceil(Math.sqrt(compIds.length));
    for (let i = 0; i < compIds.length; i++) {
      const col = i % cols;
      const row = Math.floor(i / cols);
      positions.set(compIds[i], {
        x: col * (COMPONENT_NODE_W + HORIZONTAL_GAP),
        y: row * (COMPONENT_NODE_H + HORIZONTAL_GAP),
      });
    }
    const totalCols = Math.min(compIds.length, cols);
    const totalRows = Math.ceil(compIds.length / cols);
    return {
      moduleId: mod.id,
      positions,
      bbox: {
        w: totalCols * COMPONENT_NODE_W + (totalCols - 1) * HORIZONTAL_GAP,
        h: totalRows * COMPONENT_NODE_H + (totalRows - 1) * HORIZONTAL_GAP,
      },
      intraEdgeCount: 0,
    };
  }

  // Find connected subgraphs and lay each out independently
  const subgraphs = findConnectedComponents(connected, adj);
  const allPositions = new Map<number, { x: number; y: number }>();
  let offsetX = 0;
  let maxH = 0;

  for (const subNodes of subgraphs) {
    // Layer assignment via BFS from highest-degree node
    const layers = assignLayers(subNodes, adj, weightedDegree);
    const layerGroups = groupByLayer(layers);
    const orderedLayers = orderLayers(layerGroups, adj);

    // Position nodes
    const maxLayer = Math.max(...orderedLayers.keys());
    let subMaxW = 0;

    for (let l = 0; l <= maxLayer; l++) {
      const nodes = orderedLayers.get(l) ?? [];
      const layerWidth = nodes.length * COMPONENT_NODE_W + (nodes.length - 1) * HORIZONTAL_GAP;
      subMaxW = Math.max(subMaxW, layerWidth);
    }

    // Center each layer within the subgraph width
    for (let l = 0; l <= maxLayer; l++) {
      const nodes = orderedLayers.get(l) ?? [];
      const layerWidth = nodes.length * COMPONENT_NODE_W + (nodes.length - 1) * HORIZONTAL_GAP;
      const startX = offsetX + (subMaxW - layerWidth) / 2;
      for (let i = 0; i < nodes.length; i++) {
        allPositions.set(nodes[i], {
          x: startX + i * (COMPONENT_NODE_W + HORIZONTAL_GAP),
          y: l * LAYER_GAP,
        });
      }
    }

    const subH = (maxLayer + 1) * LAYER_GAP - LAYER_GAP + COMPONENT_NODE_H;
    maxH = Math.max(maxH, subH);
    offsetX += subMaxW + HORIZONTAL_GAP * 2;
  }

  // Place disconnected nodes in grid below the hierarchy
  if (disconnected.length > 0) {
    const gridY = maxH + LAYER_GAP;
    const cols = Math.ceil(Math.sqrt(disconnected.length));
    for (let i = 0; i < disconnected.length; i++) {
      const col = i % cols;
      const row = Math.floor(i / cols);
      allPositions.set(disconnected[i], {
        x: col * (COMPONENT_NODE_W + HORIZONTAL_GAP),
        y: gridY + row * (COMPONENT_NODE_H + HORIZONTAL_GAP),
      });
    }
    const gridRows = Math.ceil(disconnected.length / cols);
    maxH = gridY + gridRows * COMPONENT_NODE_H + (gridRows - 1) * HORIZONTAL_GAP;
  }

  // Normalize positions so top-left is (0, 0) + compute bbox
  let minX = Infinity, minY = Infinity, maxXEnd = -Infinity, maxYEnd = -Infinity;
  for (const pos of allPositions.values()) {
    minX = Math.min(minX, pos.x);
    minY = Math.min(minY, pos.y);
    maxXEnd = Math.max(maxXEnd, pos.x + COMPONENT_NODE_W);
    maxYEnd = Math.max(maxYEnd, pos.y + COMPONENT_NODE_H);
  }
  const positions = new Map<number, { x: number; y: number }>();
  for (const [id, pos] of allPositions) {
    positions.set(id, { x: pos.x - minX, y: pos.y - minY });
  }

  return {
    moduleId: mod.id,
    positions,
    bbox: { w: maxXEnd - minX, h: maxYEnd - minY },
    intraEdgeCount: edgeCount,
  };
}

// ── Phase 2: Module group positioning ──

function layoutModuleGroups(
  layouts: ModuleLayout[],
  data: MapData,
  crossEdges: { source_id: number; target_id: number; weight?: number }[],
  compToModule: Map<number, number>,
): Map<number, { x: number; y: number }> {
  // Build group simulation nodes with padded dimensions
  const groupNodes: GroupSimNode[] = layouts.map((layout) => {
    const padding = GROUP_PADDING * (1 + 0.15 * Math.min(layout.intraEdgeCount, 20));
    return {
      id: `group-${layout.moduleId}`,
      moduleId: layout.moduleId,
      w: layout.bbox.w + padding * 2,
      h: layout.bbox.h + padding * 2 + GROUP_LABEL_HEIGHT,
      degree: 0,
    };
  });

  const nodeByModuleId = new Map(groupNodes.map((gn) => [gn.moduleId, gn]));

  // Aggregate cross-module edges at module-pair level
  const modulePairCount = new Map<string, number>();
  for (const e of crossEdges) {
    const srcMod = compToModule.get(e.source_id);
    const tgtMod = compToModule.get(e.target_id);
    if (srcMod === undefined || tgtMod === undefined || srcMod === tgtMod) continue;
    const key = srcMod < tgtMod ? `${srcMod}-${tgtMod}` : `${tgtMod}-${srcMod}`;
    modulePairCount.set(key, (modulePairCount.get(key) ?? 0) + 1);
  }
  for (const e of data.module_edges) {
    const key = e.source_id < e.target_id
      ? `${e.source_id}-${e.target_id}`
      : `${e.target_id}-${e.source_id}`;
    modulePairCount.set(key, (modulePairCount.get(key) ?? 0) + e.weight);
  }

  // Build group links
  const groupLinks: (SimulationLinkDatum<GroupSimNode> & { distance: number; linkStrength: number })[] = [];
  for (const [key, count] of modulePairCount) {
    const [srcId, tgtId] = key.split("-").map(Number);
    const source = nodeByModuleId.get(srcId);
    const target = nodeByModuleId.get(tgtId);
    if (!source || !target) continue;
    source.degree += count;
    target.degree += count;
    const srcDiag = Math.sqrt(source.w ** 2 + source.h ** 2) / 2;
    const tgtDiag = Math.sqrt(target.w ** 2 + target.h ** 2) / 2;
    const minDist = srcDiag + tgtDiag + GROUP_GAP;
    const linkStrength = Math.min(0.3 + 0.1 * Math.log2(count + 1), 0.7);
    groupLinks.push({ source, target, distance: minDist, linkStrength });
  }

  const maxDegree = Math.max(...groupNodes.map((gn) => gn.degree), 1);
  const baseArea = COMPONENT_NODE_W * COMPONENT_NODE_H;

  const simulation = forceSimulation(groupNodes)
    .force(
      "link",
      forceLink<GroupSimNode, (typeof groupLinks)[0]>(groupLinks)
        .distance((d) => d.distance)
        .strength((d) => d.linkStrength)
    )
    .force(
      "charge",
      forceManyBody<GroupSimNode>().strength((d) => {
        const area = d.w * d.h;
        return REPULSION_STRENGTH * 1.5 * Math.max(area / baseArea, 1);
      })
    )
    .force(
      "collide",
      forceCollide<GroupSimNode>((d) => Math.sqrt(d.w ** 2 + d.h ** 2) / 2 + GROUP_GAP / 2)
        .strength(1)
        .iterations(3)
    )
    .force("center", forceCenter(0, 0).strength(0.05))
    .force("x", forceX<GroupSimNode>(0).strength((d) =>
      d.degree === 0 ? 0.15 : 0.02 + 0.08 * (d.degree / maxDegree)
    ))
    .force("y", forceY<GroupSimNode>(0).strength((d) =>
      d.degree === 0 ? 0.15 : 0.02 + 0.08 * (d.degree / maxDegree)
    ))
    .stop();

  for (let i = 0; i < 150; i++) simulation.tick();

  // Return top-left corner of each group
  const groupPositions = new Map<number, { x: number; y: number }>();
  for (const gn of groupNodes) {
    groupPositions.set(gn.moduleId, {
      x: (gn.x ?? 0) - gn.w / 2,
      y: (gn.y ?? 0) - gn.h / 2,
    });
  }
  return groupPositions;
}

// ── Transform for L3 (components within module groups) ──

function buildL3(
  data: MapData,
  filters: EdgeFilters
): { nodes: Node[]; edges: Edge<MapEdgeData>[] } {
  const componentIds = new Set(
    data.modules.flatMap((m) => m.components.map((c) => c.id))
  );

  // Map component id -> module id
  const compToModule = new Map<number, number>();
  for (const mod of data.modules) {
    for (const comp of mod.components) {
      compToModule.set(comp.id, mod.id);
    }
  }

  // Filter component edges
  const filteredEdges = data.component_edges.filter(
    (e) =>
      componentIds.has(e.source_id) &&
      componentIds.has(e.target_id) &&
      filters.types.has(e.edge_type) &&
      e.weight >= filters.minWeight
  );

  // Partition into intra-module and cross-module edges
  const intraEdges = new Map<number, typeof filteredEdges>();
  const crossEdges: typeof filteredEdges = [];
  for (const e of filteredEdges) {
    const srcMod = compToModule.get(e.source_id);
    const tgtMod = compToModule.get(e.target_id);
    if (srcMod === tgtMod && srcMod !== undefined) {
      const arr = intraEdges.get(srcMod) || [];
      arr.push(e);
      intraEdges.set(srcMod, arr);
    } else {
      crossEdges.push(e);
    }
  }

  // ── Phase 1: Per-module internal layout ──
  const moduleLayouts: ModuleLayout[] = [];
  for (const mod of data.modules) {
    if (mod.components.length === 0) continue;
    moduleLayouts.push(layoutModule(mod, intraEdges.get(mod.id) || []));
  }

  // ── Phase 2: Module group positioning ──
  const groupPositions = layoutModuleGroups(moduleLayouts, data, crossEdges, compToModule);

  // ── Phase 3: Compose final positions + build ReactFlow nodes ──
  const nodes: Node[] = [];
  const nodeMap = new Map<string, { x: number; y: number; w: number; h: number }>();
  const boxes: GroupBox[] = [];

  for (const layout of moduleLayouts) {
    const mod = data.modules.find((m) => m.id === layout.moduleId)!;
    const groupPos = groupPositions.get(layout.moduleId)!;
    const padding = GROUP_PADDING * (1 + 0.15 * Math.min(layout.intraEdgeCount, 20));

    const box: GroupBox = {
      moduleId: mod.id,
      x: groupPos.x,
      y: groupPos.y,
      w: layout.bbox.w + padding * 2,
      h: layout.bbox.h + padding * 2 + GROUP_LABEL_HEIGHT,
    };
    boxes.push(box);

    // Group background node
    const groupNode: Node<GroupNodeData> = {
      id: `group-module-${mod.id}`,
      type: "group",
      position: { x: box.x, y: box.y },
      data: {
        label: mod.name,
        color: classificationColor(mod.classification),
      },
      style: {
        width: box.w,
        height: box.h,
        backgroundColor: `${classificationColor(mod.classification)}08`,
        borderColor: `${classificationColor(mod.classification)}40`,
        borderWidth: 2,
        borderStyle: "solid" as const,
        borderRadius: 16,
      },
    };
    nodes.push(groupNode);

    nodeMap.set(`group-module-${mod.id}`, { x: box.x, y: box.y, w: box.w, h: box.h });

    // Component nodes — absolute positions from Phase 1 relative + Phase 2 group
    for (const comp of mod.components) {
      const relPos = layout.positions.get(comp.id);
      if (!relPos) continue;

      const absX = box.x + padding + relPos.x;
      const absY = box.y + padding + GROUP_LABEL_HEIGHT + relPos.y;

      nodeMap.set(`component-${comp.id}`, {
        x: absX,
        y: absY,
        w: COMPONENT_NODE_W,
        h: COMPONENT_NODE_H,
      });

      const compNode: Node<ComponentNodeData> = {
        id: `component-${comp.id}`,
        type: "mapNode",
        position: {
          x: absX - box.x,
          y: absY - box.y,
        },
        parentId: `group-module-${mod.id}`,
        extent: "parent" as const,
        data: {
          label: comp.name,
          moduleName: mod.name,
          purpose: comp.purpose,
          confidence: comp.confidence,
          decisions: comp.decisions,
          fileCount: comp.files.length,
          files: comp.files,
        },
      };
      nodes.push(compNode);
    }
  }

  // Component edges
  const edges: Edge<MapEdgeData>[] = filteredEdges.map((e) => {
    const metadata = e.metadata || {};
    return {
      id: `edge-comp-${e.source_id}-${e.target_id}-${e.edge_type}`,
      source: `component-${e.source_id}`,
      target: `component-${e.target_id}`,
      type: "animatedEdge",
      data: {
        edgeType: e.edge_type,
        weight: e.weight,
        label: metadata.label as string | undefined,
        description: (metadata.description || metadata.label) as string | undefined,
        isHighlighted: false,
        isDimmed: false,
      },
    };
  });

  // Module edges between group boxes
  const moduleIds = new Set(boxes.map((b) => b.moduleId));
  const moduleEdges: Edge<MapEdgeData>[] = data.module_edges
    .filter(
      (e) =>
        moduleIds.has(e.source_id) &&
        moduleIds.has(e.target_id) &&
        filters.types.has(e.edge_type) &&
        e.weight >= filters.minWeight
    )
    .map((e) => {
      const metadata = e.metadata || {};
      return {
        id: `edge-module-${e.source_id}-${e.target_id}-${e.edge_type}`,
        source: `group-module-${e.source_id}`,
        target: `group-module-${e.target_id}`,
        type: "animatedEdge",
        data: {
          edgeType: e.edge_type,
          weight: e.weight,
          label: metadata.label as string | undefined,
          description: (metadata.description || metadata.label) as string | undefined,
          isHighlighted: false,
          isDimmed: false,
          isModuleEdge: true,
        },
      };
    });

  const allEdges = [...edges, ...moduleEdges];
  return { nodes, edges: assignEdgeHandles(allEdges, nodeMap) };
}

// ── Public API ──

export async function transformMapData(
  data: MapData,
  level: ViewLevel,
  filters: EdgeFilters
): Promise<{ nodes: Node[]; edges: Edge[] }> {
  if (level === "L2") {
    return buildL2(data, filters);
  }
  return buildL3(data, filters);
}

export function getEdgeTypes(data: MapData): string[] {
  const types = new Set<string>();
  for (const e of data.module_edges) types.add(e.edge_type);
  for (const e of data.component_edges) types.add(e.edge_type);
  return Array.from(types).sort();
}

export function defaultEdgeFilters(data: MapData): EdgeFilters {
  return {
    types: new Set(getEdgeTypes(data)),
    minWeight: 0,
  };
}

export function hasComponents(data: MapData): boolean {
  return data.modules.some((m) => m.components.length > 0);
}

export function getWeightRange(
  data: MapData,
  level: ViewLevel
): { min: number; max: number } {
  const edges = level === "L2" ? data.module_edges : data.component_edges;
  if (edges.length === 0) return { min: 0, max: 1 };
  const weights = edges.map((e) => e.weight);
  return { min: 0, max: Math.ceil(Math.max(...weights)) };
}
