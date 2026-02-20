// Doc: Natural_Language_Code/Frontend/info_frontend.md
// Force-directed graph layout using d3-force.
// Highly-connected nodes naturally gravitate to the center.

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
import type { Node } from "@xyflow/react";
import type { MapData, ModuleNodeData } from "./types";

// ── Layout constants ──
const NODE_W = 2400;
const NODE_H = 700;
// Collision radius — half the diagonal so rectangular nodes don't overlap
const COLLIDE_RADIUS = Math.sqrt(NODE_W ** 2 + NODE_H ** 2) / 2 + 100;
const LINK_DISTANCE = 3200;
const REPULSION_STRENGTH = -80000;
const TICKS = 200;

interface SimNode extends SimulationNodeDatum {
  id: string;
  moduleId: number;
  degree: number;
}

export function autoLayoutModules(
  data: MapData
): Node<ModuleNodeData>[] {
  const moduleIdSet = new Set(data.modules.map((m) => m.id));

  // Count degree per module
  const degreeMap = new Map<number, number>();
  for (const e of data.module_edges) {
    if (moduleIdSet.has(e.source_id) && moduleIdSet.has(e.target_id)) {
      degreeMap.set(e.source_id, (degreeMap.get(e.source_id) ?? 0) + 1);
      degreeMap.set(e.target_id, (degreeMap.get(e.target_id) ?? 0) + 1);
    }
  }

  // Build simulation nodes
  const simNodes: SimNode[] = data.modules.map((mod) => ({
    id: `module-${mod.id}`,
    moduleId: mod.id,
    degree: degreeMap.get(mod.id) ?? 0,
  }));

  const nodeById = new Map(simNodes.map((n) => [n.id, n]));

  // Build deduplicated links
  const seenEdges = new Set<string>();
  const simLinks: SimulationLinkDatum<SimNode>[] = data.module_edges
    .filter((e) => moduleIdSet.has(e.source_id) && moduleIdSet.has(e.target_id))
    .flatMap((e) => {
      const key = `${e.source_id}-${e.target_id}`;
      if (seenEdges.has(key)) return [];
      seenEdges.add(key);
      const source = nodeById.get(`module-${e.source_id}`);
      const target = nodeById.get(`module-${e.target_id}`);
      if (!source || !target) return [];
      return [{ source, target }];
    });

  // Find max degree for center-pull scaling
  const maxDegree = Math.max(...simNodes.map((n) => n.degree), 1);

  // Run simulation
  const simulation = forceSimulation(simNodes)
    .force(
      "link",
      forceLink<SimNode, SimulationLinkDatum<SimNode>>(simLinks)
        .distance(LINK_DISTANCE)
        .strength(0.3)
    )
    .force("charge", forceManyBody<SimNode>().strength(REPULSION_STRENGTH))
    .force("collide", forceCollide<SimNode>((d) =>
      COLLIDE_RADIUS * (1 + 0.02 * Math.min(d.degree, 20))
    ).strength(1))
    .force("center", forceCenter(0, 0).strength(0.05))
    // Pull nodes toward center — isolated nodes (no edges) get stronger pull
    // so they stay near the cluster instead of being flung to the periphery
    .force("x", forceX<SimNode>(0).strength((d) =>
      d.degree === 0 ? 0.12 : 0.02 + 0.08 * (d.degree / maxDegree)
    ))
    .force("y", forceY<SimNode>(0).strength((d) =>
      d.degree === 0 ? 0.12 : 0.02 + 0.08 * (d.degree / maxDegree)
    ))
    .stop();

  // Run synchronously
  for (let i = 0; i < TICKS; i++) simulation.tick();

  // Convert to ReactFlow nodes
  return simNodes.map((simNode) => {
    const mod = data.modules.find((m) => m.id === simNode.moduleId)!;
    return {
      id: simNode.id,
      type: "mapNode",
      position: {
        x: simNode.x ?? 0,
        y: simNode.y ?? 0,
      },
      data: {
        label: mod.name,
        moduleType: mod.type,
        technology: mod.technology,
        classification: mod.classification,
        deploymentTarget: mod.deployment_target,
        decisions: mod.decisions,
        componentCount: mod.components.length,
        directories: mod.directories,
        components: mod.components,
      },
    };
  });
}
