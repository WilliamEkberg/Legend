// Doc: Natural_Language_Code/Frontend/info_frontend.md

import { useState, useEffect, useMemo, useCallback, useRef } from "react";
import {
  ReactFlow,
  ReactFlowProvider,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  useReactFlow,
  type Node,
  type Edge,
  type Connection,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Link } from "react-router-dom";

import {
  fetchMap,
  fetchChangeRecords,
  fetchValidationSummary,
  generateTickets,
  fetchTickets,
  createModuleEdge,
  createComponentEdge,
  exportMapAsFile,
  createManualVersion,
} from "../../api/client";
import type {
  MapData,
  MapDecision,
  ViewLevel,
  EdgeFilters,
  SelectedNode,
  ComponentNodeData,
  ChangeRecord,
  GeneratedTicket,
  ValidationSummary,
} from "../../data/types";
import {
  transformMapData,
  getEdgeTypes,
  defaultEdgeFilters,
  hasComponents,
  getWeightRange,
} from "../../data/mapTransform";
import { MapNode } from "./MapNode";
import { GroupNode } from "./GroupNode";
import { AnimatedEdge } from "./AnimatedEdge";
import { DetailPanel } from "./DetailPanel";
import { MapSidebar } from "./MapSidebar";
import { TicketPanel } from "./TicketPanel";
import { VersionPanel } from "./VersionPanel";
import { CreateNodeModal } from "./CreateNodeModal";
import { EdgeLabelPopup } from "./EdgeLabelPopup";
import "./graph.css";

const nodeTypes = {
  mapNode: MapNode,
  group: GroupNode,
};

const edgeTypes = {
  animatedEdge: AnimatedEdge,
};

function MapViewInner() {
  const [mapData, setMapData] = useState<MapData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [level, setLevel] = useState<ViewLevel>("L2");
  const [filters, setFilters] = useState<EdgeFilters>({
    types: new Set<string>(),
    minWeight: 0,
  });
  const [selectedNode, setSelectedNode] = useState<SelectedNode>(null);
  const [searchQuery, setSearchQuery] = useState("");

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  const [changeRecords, setChangeRecords] = useState<ChangeRecord[]>([]);
  const [validationSummary, setValidationSummary] = useState<ValidationSummary | null>(null);
  const [tickets, setTickets] = useState<GeneratedTicket[]>([]);
  const [ticketMapCorrections, setTicketMapCorrections] = useState(0);
  const [showTickets, setShowTickets] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [showCreateNode, setShowCreateNode] = useState(false);
  const [pendingConnection, setPendingConnection] = useState<Connection | null>(null);
  const [exporting, setExporting] = useState(false);
  const [showVersions, setShowVersions] = useState(false);
  const [savingSnapshot, setSavingSnapshot] = useState(false);
  const [loadingTickets, setLoadingTickets] = useState(false);

  const { fitView } = useReactFlow();
  const fitViewRef = useRef(fitView);
  fitViewRef.current = fitView;

  // Fetch map data on mount
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");

    fetchMap()
      .then((data) => {
        if (cancelled) return;
        setMapData(data);
        setFilters(defaultEdgeFilters(data));
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "Failed to load map");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const refreshChangeRecords = useCallback(() => {
    fetchChangeRecords()
      .then((data) => setChangeRecords(data.records))
      .catch(() => {/* silently ignore if DB not ready */});
  }, []);

  // Fetch change records on mount
  useEffect(() => {
    refreshChangeRecords();
  }, [refreshChangeRecords]);

  const refreshValidation = useCallback(() => {
    fetchValidationSummary()
      .then(setValidationSummary)
      .catch(() => {/* silently ignore */});
  }, []);

  // Fetch validation summary on mount
  useEffect(() => {
    refreshValidation();
  }, [refreshValidation]);

  const handleGenerateTickets = useCallback(async () => {
    const apiKey = localStorage.getItem("legend:apiKey") ?? "";
    if (!apiKey) {
      alert("No API key found. Enter your API key in the launcher first.");
      return;
    }
    setGenerating(true);
    try {
      const result = await generateTickets(apiKey);
      setTickets(result.tickets);
      setTicketMapCorrections(result.map_corrections);
      setShowTickets(true);
      // Baseline advanced — refresh change records (should now be empty)
      refreshChangeRecords();
    } catch (e) {
      alert(`Ticket generation failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setGenerating(false);
    }
  }, [refreshChangeRecords]);

  const handleViewTickets = useCallback(async () => {
    setLoadingTickets(true);
    try {
      const result = await fetchTickets();
      setTickets(
        result.tickets.map((t) => ({
          id: t.id,
          title: t.title,
          description: t.description,
          acceptance_criteria: t.acceptance_criteria,
          affected_files: t.files,
        })),
      );
      setTicketMapCorrections(0);
      setShowTickets(true);
    } catch (e) {
      alert(`Failed to load tickets: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoadingTickets(false);
    }
  }, []);

  const refreshMap = useCallback(() => {
    fetchMap()
      .then((data) => {
        setMapData(data);
        setFilters(defaultEdgeFilters(data));
      })
      .catch(() => {/* silently ignore */});
  }, []);

  const handleExportMap = useCallback(async () => {
    setExporting(true);
    try {
      await exportMapAsFile();
    } catch (e) {
      alert(`Export failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setExporting(false);
    }
  }, []);

  const handleSaveSnapshot = useCallback(async () => {
    setSavingSnapshot(true);
    try {
      await createManualVersion();
    } catch (e) {
      alert(`Snapshot failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSavingSnapshot(false);
    }
  }, []);

  const onConnect = useCallback(
    (connection: Connection) => {
      if (connection.source && connection.target && connection.source !== connection.target) {
        setPendingConnection(connection);
      }
    },
    [],
  );

  const isValidConnection = useCallback(
    (connection: Edge | Connection) => {
      return connection.source !== connection.target;
    },
    [],
  );

  const handleEdgeSave = useCallback(
    async (edgeType: string, label: string) => {
      if (!pendingConnection) return;
      const { source, target } = pendingConnection;
      if (!source || !target) return;
      try {
        if (level === "L2") {
          const sourceId = parseInt(source.replace("module-", ""), 10);
          const targetId = parseInt(target.replace("module-", ""), 10);
          await createModuleEdge({
            source_id: sourceId,
            target_id: targetId,
            edge_type: edgeType,
            label: label || undefined,
          });
        } else if (source.startsWith("group-module-")) {
          // L3: connecting group nodes → module edge
          const sourceId = parseInt(source.replace("group-module-", ""), 10);
          const targetId = parseInt(target.replace("group-module-", ""), 10);
          await createModuleEdge({
            source_id: sourceId,
            target_id: targetId,
            edge_type: edgeType,
            label: label || undefined,
          });
        } else {
          // L3: connecting component nodes → component edge
          const sourceId = parseInt(source.replace("component-", ""), 10);
          const targetId = parseInt(target.replace("component-", ""), 10);
          await createComponentEdge({
            source_id: sourceId,
            target_id: targetId,
            edge_type: edgeType,
            label: label || undefined,
          });
        }
        refreshMap();
      } catch (e) {
        alert(`Failed to create edge: ${e instanceof Error ? e.message : String(e)}`);
      }
      setPendingConnection(null);
    },
    [pendingConnection, level, refreshMap],
  );

  // Recompute nodes/edges when data, level, or filters change
  useEffect(() => {
    if (!mapData) return;
    let cancelled = false;

    transformMapData(mapData, level, filters)
      .then(({ nodes: n, edges: e }) => {
        if (cancelled) return;
        console.log("[MapView] transform result:", n.length, "nodes,", e.length, "edges");
        setNodes(n);
        setEdges(e);
        setSelectedNode(null);
      })
      .catch((err) => {
        if (cancelled) return;
        console.error("[MapView] Layout error:", err);
      });

    return () => {
      cancelled = true;
    };
  }, [mapData, level, filters, setNodes, setEdges]);

  // Derive set of node IDs that have pending decision changes
  const changedNodeIds = useMemo(() => {
    const ids = new Set<string>();
    console.log("[MapView] changeRecords:", changeRecords.length, changeRecords.map(r => ({ id: r.id, context: r.context })));
    for (const rec of changeRecords) {
      if (rec.context?.module_id != null) ids.add(`module-${rec.context.module_id}`);
      if (rec.context?.component_id != null) ids.add(`component-${rec.context.component_id}`);
    }
    console.log("[MapView] changedNodeIds:", [...ids]);
    return ids;
  }, [changeRecords]);

  // Derive set of node IDs with re-validation changes
  const revalidatedNodeIds = useMemo(() => {
    const ids = new Set<string>();
    if (!validationSummary) return ids;
    for (const mid of validationSummary.affected_module_ids) {
      ids.add(`module-${mid}`);
    }
    for (const cid of validationSummary.affected_component_ids) {
      ids.add(`component-${cid}`);
    }
    return ids;
  }, [validationSummary]);

  // Apply search dimming + changed-node flag + revalidation flag
  const styledNodes = useMemo(() => {
    const q = searchQuery.toLowerCase().trim();
    return nodes.map((node) => {
      const hasChanges = changedNodeIds.has(node.id);
      const hasRevalidation = revalidatedNodeIds.has(node.id);
      const label = (node.data as { label?: string }).label ?? "";
      const matches = !q || label.toLowerCase().includes(q) || node.type === "group";
      return {
        ...node,
        data: { ...node.data, hasChanges, hasRevalidation },
        style: {
          ...node.style,
          ...(q ? { opacity: matches ? 1 : 0.25 } : {}),
        },
      };
    });
  }, [nodes, searchQuery, changedNodeIds, revalidatedNodeIds]);

  // Apply search dimming and selection highlighting to edges
  const styledEdges = useMemo(() => {
    const q = searchQuery.toLowerCase();

    // Build set of matching node IDs for search dimming
    const matchingNodeIds = new Set<string>();
    if (q.trim()) {
      nodes
        .filter((node) => {
          const label = (node.data as { label?: string }).label ?? "";
          return label.toLowerCase().includes(q);
        })
        .forEach((node) => matchingNodeIds.add(node.id));
    }

    // Get selected node ID for highlighting
    const selectedNodeId = selectedNode
      ? selectedNode.kind === "module"
        ? `module-${selectedNode.module.id}`
        : `component-${selectedNode.component.id}`
      : null;

    return edges.map((edge) => {
      const isConnected =
        selectedNodeId &&
        (edge.source === selectedNodeId || edge.target === selectedNodeId);

      const isDimmed =
        matchingNodeIds.size > 0 &&
        !matchingNodeIds.has(edge.source) &&
        !matchingNodeIds.has(edge.target);

      return {
        ...edge,
        data: {
          ...edge.data,
          isDimmed,
          isHighlighted: isConnected,
        },
      };
    });
  }, [edges, nodes, searchQuery, selectedNode]);

  // Available edge types
  const availableEdgeTypes = useMemo(
    () => (mapData ? getEdgeTypes(mapData) : []),
    [mapData]
  );

  // Dynamic weight range for slider
  const weightRange = useMemo(
    () => (mapData ? getWeightRange(mapData, level) : { min: 0, max: 1 }),
    [mapData, level]
  );

  const l3Available = mapData ? hasComponents(mapData) : false;

  // Reset to L2 if L3 selected but no components exist
  useEffect(() => {
    if (level === "L3" && !l3Available) {
      setLevel("L2");
    }
  }, [level, l3Available]);

  // Node click handler
  const onNodeClick = useCallback(
    (_event: React.MouseEvent, node: Node) => {
      if (!mapData || node.type === "group") return;

      const data = node.data;
      if ("moduleType" in data) {
        const mod = mapData.modules.find(
          (m) => `module-${m.id}` === node.id
        );
        if (mod) {
          setSelectedNode({ kind: "module", module: mod });
        }
      } else if ("moduleName" in data) {
        const compData = data as ComponentNodeData;
        for (const mod of mapData.modules) {
          const comp = mod.components.find(
            (c) => `component-${c.id}` === node.id
          );
          if (comp) {
            setSelectedNode({
              kind: "component",
              component: comp,
              moduleName: compData.moduleName,
            });
            break;
          }
        }
      }
    },
    [mapData]
  );

  const handleFitView = useCallback(() => {
    fitViewRef.current({ padding: 0.2, duration: 300 });
  }, []);

  const handleDecisionChange = useCallback(
    (kind: "module" | "component", entityId: number, newDecisions: MapDecision[]) => {
      refreshChangeRecords();
      setMapData((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          modules: prev.modules.map((mod) => {
            if (kind === "module" && mod.id === entityId) {
              return { ...mod, decisions: newDecisions };
            }
            return {
              ...mod,
              components: mod.components.map((comp) =>
                kind === "component" && comp.id === entityId
                  ? { ...comp, decisions: newDecisions }
                  : comp,
              ),
            };
          }),
        };
      });
      setSelectedNode((prev) => {
        if (!prev) return prev;
        if (prev.kind === "module" && kind === "module" && prev.module.id === entityId) {
          return { ...prev, module: { ...prev.module, decisions: newDecisions } };
        }
        if (prev.kind === "component" && kind === "component" && prev.component.id === entityId) {
          return { ...prev, component: { ...prev.component, decisions: newDecisions } };
        }
        return prev;
      });
    },
    [refreshChangeRecords],
  );

  // Empty state
  if (loading) {
    return (
      <div className="map-container">
        <div className="map-empty">
          <div className="map-spinner" />
          <p>Loading architecture map...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="map-container">
        <div className="map-empty">
          <p className="map-error">{error}</p>
          <Link to="/" className="map-back-link">
            &larr; Back to launcher
          </Link>
        </div>
      </div>
    );
  }

  if (!mapData || mapData.modules.length === 0) {
    return (
      <div className="map-container">
        <div className="map-empty">
          <p>No architecture map data found.</p>
          <p className="map-empty-hint">
            Run the pipeline first to generate the architecture map.
          </p>
          <Link to="/" className="map-back-link">
            &larr; Back to launcher
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="map-container">
      <MapSidebar
        level={level}
        onLevelChange={setLevel}
        l3Available={l3Available}
        edgeTypes={availableEdgeTypes}
        filters={filters}
        onFiltersChange={setFilters}
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        onFitView={handleFitView}
        changeCount={changeRecords.length}
        onGenerateTickets={handleGenerateTickets}
        generating={generating}
        onViewTickets={handleViewTickets}
        loadingTickets={loadingTickets}
        onExportMap={handleExportMap}
        exporting={exporting}
        weightRange={weightRange}
        onBrowseVersions={() => setShowVersions(true)}
        onSaveSnapshot={handleSaveSnapshot}
        savingSnapshot={savingSnapshot}
      />
      <div className="map-canvas">
        <div className="map-topbar">
          <Link to="/" className="map-back-link">
            &larr; Launcher
          </Link>
          <span className="map-topbar-title">
            Architecture Map — {level === "L2" ? "Modules" : "Components"}
          </span>
          <span className="map-topbar-stats">
            {styledNodes.length} nodes / {styledEdges.length} edges
          </span>
        </div>
        <div className="map-flow-wrapper">
          <ReactFlow
            nodes={styledNodes}
            edges={styledEdges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onNodeClick={onNodeClick}
            onConnect={onConnect}
            isValidConnection={isValidConnection}
            connectionRadius={200}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            fitView
            fitViewOptions={{ padding: 0.15 }}
            minZoom={0.005}
            maxZoom={1.5}
            proOptions={{ hideAttribution: true }}
          >
            <Controls
              showInteractive={false}
              className="map-controls"
            />
            <MiniMap
              nodeColor={(n) => {
                if (n.type === "group") return "transparent";
                const data = n.data as { classification?: string };
                if (data.classification === "shared-library") return "#bd93f9";
                if (data.classification === "supporting-asset") return "#ffb300";
                return "#00e5ff";
              }}
              maskColor="rgba(10, 14, 20, 0.8)"
              className="map-minimap"
            />
          </ReactFlow>
          <button
            className="map-create-btn"
            onClick={() => setShowCreateNode(true)}
          >
            + {level === "L2" ? "Module" : "Component"}
          </button>
          {showTickets && (
            <TicketPanel
              tickets={tickets}
              mapCorrections={ticketMapCorrections}
              onClose={() => setShowTickets(false)}
            />
          )}
          {showVersions && (
            <VersionPanel
              onClose={() => setShowVersions(false)}
            />
          )}
        </div>
      </div>
      <DetailPanel
        selected={selectedNode}
        onClose={() => setSelectedNode(null)}
        onDecisionChange={handleDecisionChange}
        changeRecords={changeRecords}
        validationSummary={validationSummary}
        onMutate={refreshChangeRecords}
        onDelete={() => {
          setSelectedNode(null);
          refreshMap();
          refreshChangeRecords();
        }}
      />
      {showCreateNode && mapData && (
        <CreateNodeModal
          level={level}
          modules={mapData.modules}
          onSave={() => {
            setShowCreateNode(false);
            refreshMap();
            refreshChangeRecords();
          }}
          onClose={() => setShowCreateNode(false)}
        />
      )}
      {pendingConnection && mapData && (
        <EdgeLabelPopup
          level={
            level === "L2" || (pendingConnection.source ?? "").startsWith("group-module-")
              ? "L2"
              : "L3"
          }
          sourceName={
            (() => {
              const id = pendingConnection.source ?? "";
              if (level === "L2") {
                const mod = mapData.modules.find((m) => `module-${m.id}` === id);
                return mod?.name ?? id;
              }
              if (id.startsWith("group-module-")) {
                const moduleId = parseInt(id.replace("group-module-", ""), 10);
                const mod = mapData.modules.find((m) => m.id === moduleId);
                return mod?.name ?? id;
              }
              for (const mod of mapData.modules) {
                const comp = mod.components.find((c) => `component-${c.id}` === id);
                if (comp) return comp.name;
              }
              return id;
            })()
          }
          targetName={
            (() => {
              const id = pendingConnection.target ?? "";
              if (level === "L2") {
                const mod = mapData.modules.find((m) => `module-${m.id}` === id);
                return mod?.name ?? id;
              }
              if (id.startsWith("group-module-")) {
                const moduleId = parseInt(id.replace("group-module-", ""), 10);
                const mod = mapData.modules.find((m) => m.id === moduleId);
                return mod?.name ?? id;
              }
              for (const mod of mapData.modules) {
                const comp = mod.components.find((c) => `component-${c.id}` === id);
                if (comp) return comp.name;
              }
              return id;
            })()
          }
          onSave={handleEdgeSave}
          onClose={() => setPendingConnection(null)}
        />
      )}
    </div>
  );
}

export function MapView() {
  return (
    <ReactFlowProvider>
      <MapViewInner />
    </ReactFlowProvider>
  );
}
