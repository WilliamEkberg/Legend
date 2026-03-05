// Doc: Natural_Language_Code/Frontend/info_frontend.md

import type { Node, Edge } from "@xyflow/react";

// ── Backend response types (from export_full_map) ──

export interface MapDecision {
  id: number;
  category: string;
  text: string;
  detail?: string | null;
  source: string;
}

export interface ComponentFile {
  path: string;
  is_test: boolean;
}

export interface MapComponent {
  id: number;
  name: string;
  purpose: string;
  confidence: number;
  files: ComponentFile[];
  decisions: MapDecision[];
}

export interface MapModule {
  id: number;
  name: string;
  classification: string; // "module" | "shared-library" | "supporting-asset"
  type: string;
  technology: string;
  source_origin: string;
  deployment_target: string;
  directories: string[];
  decisions: MapDecision[];
  components: MapComponent[];
}

export interface MapEdgeRaw {
  source_id: number;
  target_id: number;
  edge_type: string;
  weight: number;
  metadata: Record<string, unknown>;
}

export interface MapData {
  modules: MapModule[];
  module_edges: MapEdgeRaw[];
  component_edges: MapEdgeRaw[];
}

// ── Edge type constants (for creation dropdowns) ──

export const MODULE_EDGE_TYPES = ["depends_on", "uses_data_store", "communicates_via"] as const;
export const COMPONENT_EDGE_TYPES = ["depends-on", "call", "import", "inheritance"] as const;

// ── Frontend @xyflow types ──

export type ViewLevel = "L2" | "L3";

export interface ModuleNodeData {
  label: string;
  moduleType: string;
  technology: string;
  classification: string;
  deploymentTarget: string;
  decisions: MapDecision[];
  componentCount: number;
  directories: string[];
  components: MapComponent[];
  [key: string]: unknown;
}

export interface ComponentNodeData {
  label: string;
  moduleName: string;
  purpose: string;
  confidence: number;
  decisions: MapDecision[];
  fileCount: number;
  files: ComponentFile[];
  [key: string]: unknown;
}

export interface GroupNodeData {
  label: string;
  color: string;
  [key: string]: unknown;
}

export interface MapEdgeData {
  edgeType: string;
  weight: number;
  label?: string;
  description?: string;
  isHighlighted?: boolean;  // Edge connected to selected node
  isDimmed?: boolean;        // Edge doesn't match search query
  isModuleEdge?: boolean;    // Module edge rendered between groups in L3 view
  [key: string]: unknown;
}

export type ModuleNode = Node<ModuleNodeData, "mapNode">;
export type ComponentNode = Node<ComponentNodeData, "mapNode">;
export type GroupNode = Node<GroupNodeData, "group">;
export type MapNode = ModuleNode | ComponentNode | GroupNode;
export type MapEdge = Edge<MapEdgeData>;

// ── Filter state ──

export interface EdgeFilters {
  types: Set<string>;
  minWeight: number;
}

// ── Detail panel ──

export type SelectedNode =
  | { kind: "module"; module: MapModule }
  | { kind: "component"; component: MapComponent; moduleName: string }
  | null;

// ── Change records (Map Editor) ──

export interface ChangeRecord {
  id: number;
  entity_type: string;
  entity_id: number;
  action: "add" | "edit" | "remove";
  old_value: { category?: string; text?: string } | null;
  new_value: { category?: string; text?: string } | null;
  origin: string;
  baseline_id: number | null;
  created_at: string;
  context: {
    component_name: string | null;
    module_name: string | null;
    component_id: number | null;
    module_id: number | null;
  } | null;
}

export interface ChangeRecordsResponse {
  baseline_id: number | null;
  records: ChangeRecord[];
}

// ── Tickets ──

export interface GeneratedTicket {
  id: number;
  title: string;
  description: string;
  acceptance_criteria: string;
  affected_files: string[];
}

export interface TicketGenerateResponse {
  tickets: GeneratedTicket[];
  map_corrections: number;
  message?: string;
}

export interface SavedTicket {
  id: number;
  title: string;
  description: string;
  acceptance_criteria: string;
  status: string;
  files: string[];
  created_at: string;
}

// ── Map Versioning ──

export interface MapVersion {
  id: number;
  version_number: number;
  trigger: "part3" | "revalidation" | "manual";
  summary: {
    total_decisions: number;
    module_decisions: number;
    component_decisions: number;
    modules: number;
    components: number;
  } | null;
  created_at: string;
}

export interface VersionDecision {
  decision_id: number | null;
  module_id: number | null;
  component_id: number | null;
  module_name: string;
  component_name: string;
  category: string;
  text: string;
  source: string;
}

export interface VersionComparison {
  added: VersionDecision[];
  removed: VersionDecision[];
  changed: {
    decision_id: number;
    module_name: string;
    component_name: string;
    old: { category: string; text: string; source: string };
    new: { category: string; text: string; source: string };
  }[];
  unchanged_count: number;
  version_a: MapVersion;
  version_b: MapVersion;
}

// ── Re-validation ──

export interface DecisionValidation {
  id: number;
  decision_id: number | null;
  source: string;
  status:
    | "confirmed"
    | "updated"
    | "outdated"
    | "new"
    | "implemented"
    | "diverged"
    | "unchanged";
  old_text: string | null;
  new_text: string | null;
  reason: string | null;
  category: string;
  module_name: string;
  component_name: string;
}

export interface ValidationSummary {
  validation_run_id: number | null;
  affected_module_ids: number[];
  affected_component_ids: number[];
  decision_validations: DecisionValidation[];
  new_file_paths: string[];
}

export interface ValidationRun {
  id: number;
  before_version_id: number;
  after_version_id: number;
  model: string;
  status: string;
  summary: {
    confirmed: number;
    updated: number;
    outdated: number;
    new: number;
    implemented: number;
    diverged: number;
    unchanged: number;
  } | null;
  created_at: string;
}

// ── Chat ──

export type ChatMode = "ask" | "edit";

export interface ProposedChange {
  id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  description: string;
  status: "pending" | "applied" | "rejected";
}

export interface ChatEvent {
  type:
    | "text"
    | "tool_call"
    | "tool_result"
    | "proposed_change"
    | "error"
    | "done";
  content?: string;
  text?: string;
  name?: string;
  arguments?: Record<string, unknown>;
  result?: string;
  change?: ProposedChange;
  session_id?: string;
  proposed_changes?: ProposedChange[];
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  toolCalls?: { name: string; arguments: Record<string, unknown> }[];
  toolResults?: { name: string; result: string }[];
  proposedChanges?: ProposedChange[];
  timestamp: string;
}
