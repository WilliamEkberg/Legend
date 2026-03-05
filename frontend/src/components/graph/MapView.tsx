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
  exportLlmContext,
  createManualVersion,
  CreditError,
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
  defaultEdgeFilters,
  hasComponents,
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
import { MapErrorBoundary } from "./MapErrorBoundary";
import { ChatPanel } from "./ChatPanel";
import { MapChatBar } from "./MapChatBar";
import { ThemeToggle } from "../ThemeToggle";
import { Button } from "@/components/ui/button";

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
  const [exportingLlmContext, setExportingLlmContext] = useState(false);
  const [showVersions, setShowVersions] = useState(false);
  const [savingSnapshot, setSavingSnapshot] = useState(false);
  const [loadingTickets, setLoadingTickets] = useState(false);
  const [showChat, setShowChat] = useState(false);
  const [chatInitialMessage, setChatInitialMessage] = useState<string | undefined>();

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
      if (e instanceof CreditError) {
        alert(`API Credit Error: ${e.message}`);
      } else {
        alert(`Ticket generation failed: ${e instanceof Error ? e.message : String(e)}`);
      }
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

  const handleExportLlmContext = useCallback(async () => {
    setExportingLlmContext(true);
    try {
      await exportLlmContext();
    } catch (e) {
      alert(`LLM Context export failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setExportingLlmContext(false);
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
        setError("");
      })
      .catch((err) => {
        if (cancelled) return;
        console.error("[MapView] Layout error:", err);
        setError(
          err instanceof Error ? err.message : "Layout computation failed"
        );
        setNodes([]);
        setEdges([]);
        setSelectedNode(null);
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

  // Loading state
  if (loading) {
    return (
      <div className="flex w-screen h-screen bg-background overflow-hidden">
        <div className="flex flex-col items-center justify-center h-full w-full gap-4 text-muted-foreground">
          <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin" />
          <p>Loading architecture map...</p>
        </div>
      </div>
    );
  }

  // Error state
  if (error) {
    return (
      <div className="flex w-screen h-screen bg-background overflow-hidden">
        <div className="flex flex-col items-center justify-center h-full w-full gap-4 text-muted-foreground">
          <p className="text-destructive font-medium">{error}</p>
          {mapData && (
            <Button
              variant="outline"
              onClick={() => {
                setError("");
              }}
            >
              Retry
            </Button>
          )}
          <Link to="/" className="text-sm text-primary hover:text-primary/80 mt-2">
            &larr; Back to launcher
          </Link>
        </div>
      </div>
    );
  }

  // Empty state
  if (!mapData || mapData.modules.length === 0) {
    return (
      <div className="flex w-screen h-screen bg-background overflow-hidden">
        <div className="flex flex-col items-center justify-center h-full w-full gap-4 text-muted-foreground">
          <p>No architecture map data found.</p>
          <p className="text-sm text-muted-foreground/70">
            Run the pipeline first to generate the architecture map.
          </p>
          <Link to="/" className="text-sm text-primary hover:text-primary/80 mt-2">
            &larr; Back to launcher
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="relative flex w-screen h-screen bg-background overflow-hidden">
      <MapSidebar
        level={level}
        onLevelChange={setLevel}
        l3Available={l3Available}
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        onFitView={handleFitView}
        changeCount={changeRecords.length}
        onGenerateTickets={handleGenerateTickets}
        generating={generating}
        onViewTickets={handleViewTickets}
        loadingTickets={loadingTickets}
        onBrowseVersions={() => setShowVersions(true)}
        onSaveSnapshot={handleSaveSnapshot}
        savingSnapshot={savingSnapshot}
      />
      <div className="flex-1 flex flex-col h-full overflow-hidden">
        <div className="relative flex items-center gap-4 px-4 h-12 bg-card border-b border-border shrink-0">
          <Link to="/" className="text-sm text-primary hover:text-primary/80">
            &larr; Launcher
          </Link>
          <span className="font-medium text-sm text-foreground">
            Architecture Map — {level === "L2" ? "Modules" : "Components"}
          </span>
          <Button
            variant="outline"
            size="sm"
            className="text-xs h-7 ml-auto border-primary/30 hover:border-primary hover:bg-primary/10"
            onClick={handleExportLlmContext}
            disabled={exportingLlmContext}
          >
            {exportingLlmContext ? "Exporting…" : "Export LLM Context"}
          </Button>
          <span className="text-xs text-muted-foreground">
            {styledNodes.length} nodes / {styledEdges.length} edges
          </span>
          <Button
            variant={showChat ? "default" : "outline"}
            size="sm"
            className="text-xs h-7"
            onClick={() => setShowChat((v) => !v)}
          >
            Chat
          </Button>
          <ThemeToggle />
        </div>
        <div className="flex-1 relative overflow-hidden">
          <ReactFlow
            nodes={styledNodes}
            edges={styledEdges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onNodeClick={onNodeClick}
            onConnect={onConnect}
            isValidConnection={isValidConnection}
            connectionRadius={20}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            fitView
            fitViewOptions={{ padding: 0.15 }}
            minZoom={0.1}
            maxZoom={4}
            proOptions={{ hideAttribution: true }}
          >
            <Controls showInteractive={false} />
            <MiniMap
              nodeColor={(n) => {
                if (n.type === "group") return "transparent";
                const data = n.data as { classification?: string };
                if (data.classification === "shared-library") return "hsl(280 45% 55%)";
                if (data.classification === "supporting-asset") return "hsl(35 70% 50%)";
                return "hsl(150 30% 50%)";
              }}
              maskColor="hsl(var(--background) / 0.8)"
            />
          </ReactFlow>
          <Button
            className="absolute bottom-4 right-[220px] z-10"
            size="sm"
            onClick={() => setShowCreateNode(true)}
          >
            + {level === "L2" ? "Module" : "Component"}
          </Button>
          {!showChat && (
            <MapChatBar
              onSubmit={(message) => {
                setChatInitialMessage(message);
                setShowChat(true);
              }}
            />
          )}
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
      {showChat && (
        <ChatPanel
          onClose={() => {
            setShowChat(false);
            setChatInitialMessage(undefined);
          }}
          initialMessage={chatInitialMessage}
          onNodeSelect={(nodeType, id) => {
            const nodeId = nodeType === "module"
              ? (level === "L2" ? `module-${id}` : `group-module-${id}`)
              : `component-${id}`;
            const node = nodes.find((n) => n.id === nodeId);
            if (node && mapData) {
              if (nodeType === "module") {
                const mod = mapData.modules.find((m) => m.id === id);
                if (mod) setSelectedNode({ kind: "module", module: mod });
              } else {
                for (const mod of mapData.modules) {
                  const comp = mod.components.find((c) => c.id === id);
                  if (comp) {
                    setSelectedNode({ kind: "component", component: comp, moduleName: mod.name });
                    break;
                  }
                }
              }
              fitView({ nodes: [{ id: nodeId }], duration: 500, padding: 0.5 });
            }
          }}
          onMapMutated={() => {
            refreshMap();
            refreshChangeRecords();
          }}
        />
      )}
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
    <MapErrorBoundary>
      <ReactFlowProvider>
        <MapViewInner />
      </ReactFlowProvider>
    </MapErrorBoundary>
  );
}
