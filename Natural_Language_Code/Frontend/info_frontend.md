# Legend Desktop App (Frontend)

> Part of [Legend Architecture Map](../legend_map/info_legend_map.md)

## Section 1 (HUMAN EDITS ONLY)

### Purpose

Desktop application for the Legend pipeline. Two roles:
1. **Pipeline launcher** — configure credentials, trigger pipeline steps (Parts 1-3 + re-validation), view streaming output
2. **Architecture map viewer** — interactive graph visualization of the architecture map at L2 (modules) and L3 (components), with detail panels showing technical decisions

---

### How it Works

#### Pipeline Launcher (current)

1. `start.sh` creates Python venv, starts FastAPI on `:8000`, starts Tauri app (Vite on `:5173`)
2. User enters API key + selects provider + selects model from dropdown + enters repo path → clicks run
3. Frontend POSTs to `/api/run/stream` (SSE) → Vite proxy → FastAPI → `opencode run` subprocess
4. Output streamed as SSE events displayed in real-time (green success / red error)

#### Map Visualization

1. User opens map view → frontend requests JSON from backend (`export_full_map()` endpoint)
2. **L2 view (modules)** — Each module is a node. Edges show inter-module dependencies (L2 edges from DB). Node labels show module name + technology. Nodes auto-positioned using d3-force layout with degree-based centering, grouped by classification (module, shared-library, supporting-asset).
3. **L3 view (components)** — Switch to component level. Modules become background group containers. Components are nodes positioned within their module group. Edges show L3 component-to-component dependencies (call, import, inheritance). Edge styles distinguish edge types.
4. **Enhanced edge rendering**:
   - Bezier curves for smooth edge paths
   - Type-based coloring: call=cyan, import=purple, inheritance=green, depends-on=cyan
   - Logarithmic weight scaling (1-5px stroke width)
   - Search dimming: edges not connected to matching nodes dim to 30% opacity
   - Selection highlighting: edges connected to selected node highlight with brighter colors
5. **Node selection** — Click a node → detail panel slides in from right showing:
   - Module detail: name, type, technology, deployment target, decisions grouped by category (`cross_cutting`, `deployment`), list of components
   - Component detail: name, parent module, decisions grouped by category (`api_contracts`, `patterns`, `libraries`, `boundaries`, `error_handling`, `data_flow`), file list
6. **Edge filtering** — Toggle edge types (call/import/inheritance), filter by weight threshold
7. **Search** — Type to filter nodes; non-matching nodes and edges dim but remain visible
8. **Sidebar** — Level selector (L2/L3), edge filter controls, fit-view button

#### Rules & Edge Cases

- Empty API key → client-side error before network call
- API keys in localStorage only, passed as env vars to subprocess, never to disk
- Vite proxies `/api` to `localhost:8000`
- Tauri Rust backend is minimal shell — no commands registered, all logic in Python
- If `export_full_map()` returns empty (pipeline hasn't run), show empty state with prompt to run pipeline
- Modules with no components still appear as nodes at L2 but have no children at L3
- Edge weights from DB drive edge thickness in visualization
- Decision categories map to distinct visual sections in the detail panel

---

## Section 2 (HUMAN + AI)

### Architecture

#### Data Model — Pipeline Launcher (current)

```
Input:  api_key (string), provider (string), model (string, dropdown per provider)
State:  apiKey, provider, model (localStorage), loading (bool), result (RunResponse|null), error (string)

API:    POST /api/run
        Request:  { api_key: string, provider: string, model?: string }
        Response: { success: boolean, output: string, error: string }
```

#### Data Model — Map Visualization

```
Backend provides (from export_full_map):
{
  modules: [
    {
      id, name, classification, type, technology, source_origin, deployment_target,
      directories: [path, ...],
      decisions: [{id, category, text, source}, ...],
      components: [
        {
          id, name, purpose, confidence,
          files: [{path, is_test}, ...],
          decisions: [{id, category, text, source}, ...]
        }
      ]
    }
  ],
  module_edges: [{source_id, target_id, edge_type, weight, metadata}, ...],
  component_edges: [{source_id, target_id, edge_type, weight, metadata}, ...]
}
```

Note: `classification` is `"module"`, `"shared-library"`, or `"supporting-asset"`. Edge data comes from two separate DB tables (`module_edges` and `component_edges`) instead of a single `edges` table.

```
Frontend transforms to @xyflow/react:

ReactFlow Node (module, L2):
    id: "module-{id}"
    type: "custom"
    data: { label, moduleType, technology, decisions[], componentCount }
    position: { x, y }  -- computed by layout

ReactFlow Node (component, L3):
    id: "component-{id}"
    type: "custom"
    data: { label, moduleName, decisions[], fileCount }
    position: { x, y }  -- within parent group
    parentId: "group-module-{module_id}"

ReactFlow Node (group background, L3):
    id: "group-module-{module_id}"
    type: "group"
    data: { label: module.name, color }
    style: { width, height }  -- computed from child count

ReactFlow Edge:
    id: "edge-{source}-{target}-{type}"
    source, target: node IDs
    type: "animatedEdge"
    data: {
      edgeType,
      weight,
      label?,
      description?,
      isHighlighted?,  -- connected to selected node
      isDimmed?        -- doesn't match search query
    }
```

#### System Flow

```
start.sh
  ├── uvicorn (FastAPI on :8000)
  │     ├── POST /api/run        → opencode subprocess
  │     ├── GET  /api/map        → export_full_map(conn) → JSON
  │     └── POST /api/map/import → import_full_map(conn, data) → summary
  └── npm run tauri:dev
        └── Vite (:5173) → Tauri webview

Pipeline launcher:
  [App form] → POST /api/run → [Vite proxy] → [FastAPI] → [opencode subprocess]

Map visualization:
  [MapView] → GET /api/map → [Vite proxy] → [FastAPI] → [db.export_full_map()] → JSON
  [MapView] → transform to @xyflow nodes/edges → [ReactFlow] → render
  [Node click] → [DetailPanel] slides in → shows decisions by category
```

#### Key Components — Current (launcher)

| File | Responsibility |
|------|----------------|
| `frontend/src/App.tsx` | Terminal UI: ASCII logo, form, run button, output display, full pipeline orchestration (chains parts 1–3 sequentially with progress indicator) |
| `frontend/src/api/client.ts` | `runOpenCode()` fetch wrapper, request/response types |
| `frontend/src/main.tsx` | React entry point |
| `frontend/src/index.css` | Tailwind CSS: `@tailwind` directives, HSL design tokens (light + dark), node/edge color vars, scrollbar styles, React Flow overrides |
| `frontend/src/lib/utils.ts` | `cn()` utility (clsx + tailwind-merge) for conditional class names |
| `frontend/src/components/ThemeToggle.tsx` | Light/dark mode toggle button (Sun/Moon icons via lucide-react, useTheme from next-themes) |
| `frontend/src/components/ui/*.tsx` | shadcn/ui components: button, badge, card, input, label, select, tabs, scroll-area, switch, slider, separator, dialog, dropdown-menu, tooltip, sonner, skeleton |
| `frontend/tailwind.config.ts` | Tailwind CSS config: HSL color tokens, font families, animations, content paths |
| `frontend/postcss.config.js` | PostCSS config: tailwindcss + autoprefixer plugins |
| `frontend/components.json` | shadcn/ui config (rsc: false, aliases, style: default) |
| `frontend/src-tauri/src/lib.rs` | Tauri runtime init + debug logging |
| `frontend/src-tauri/tauri.conf.json` | Window config (720x700), dev URL, build commands |
| `frontend/vite.config.ts` | React plugin, `/api` proxy to localhost:8000 |
| `start.sh` | Full-stack startup: venv, backend, Tauri |

#### Key Components — Map visualization (to build)

| File | Responsibility |
|------|----------------|
| `frontend/src/components/graph/MapView.tsx` | Main orchestrator: fetches map data, manages level state, computes styled edges (dimming/highlighting), renders ReactFlow |
| `frontend/src/components/graph/MapNode.tsx` | Custom @xyflow node: renders module (L2) or component (L3) with label + type badge |
| `frontend/src/components/graph/GroupNode.tsx` | Background container node for module groups at L3 level |
| `frontend/src/components/graph/AnimatedEdge.tsx` | Custom edge: Bezier curves, type-based colors, logarithmic weight scaling, SVG-based labels |
| `frontend/src/components/graph/DetailPanel.tsx` | Right-side slide-in panel: decisions grouped by category, metadata, connections |
| `frontend/src/components/graph/MapSidebar.tsx` | Left sidebar: L2/L3 level toggle, edge type filters, weight threshold, search, fit-view, export map JSON |
| `frontend/src/data/mapTransform.ts` | Transform `export_full_map` JSON → @xyflow nodes + edges (async, uses auto-layout for L2) |
| `frontend/src/data/autoLayout.ts` | d3-force automatic graph layout: degree-based centering pulls connected nodes to center, isolated nodes get stronger pull to stay near cluster |
| `frontend/src/data/types.ts` | TypeScript interfaces for map data, node data, edge data |
| `frontend/src/api/client.ts` | `fetchMap()`, `exportMapAsFile()`, `importMap()`, `runOpenCode()`, `runOpenCodeStream()`, `updateDecision()`, `createDecision()`, `deleteDecision()`, `fetchChangeRecords()`, `generateTickets()`, `fetchTickets()`, `createModule()`, `createComponent()`, `createModuleEdge()`, `createComponentEdge()`, `fetchVersions()`, `fetchVersion()`, `createManualVersion()`, `compareVersions()`, `fetchValidationRuns()`, `fetchValidationRun()`, `fetchValidationSummary()` |
| `frontend/src/components/graph/TicketPanel.tsx` | Sliding panel: ticket list with copy-as-markdown per ticket, map correction count, triggered by "Generate Tickets" button |
| `frontend/src/components/graph/CreateNodeModal.tsx` | Modal for creating new modules (L2) or components (L3) with form fields matching DB schema |
| `frontend/src/components/graph/EdgeLabelPopup.tsx` | Modal triggered by handle drag (onConnect): edge type dropdown + optional label, then persists via API |
| `frontend/src/components/graph/VersionPanel.tsx` | Bottom slide-up panel: version list with compare dropdown, version detail (decisions grouped by module), version comparison (added/removed/changed diff view) |

---

### Implementation

#### Entry Points

- `start.sh` — full-stack startup
- `npm run tauri:dev` — Tauri desktop (requires backend)
- `npm run dev` — Vite browser mode (requires backend)

#### Dependencies

- Core npm: `react` ^19.2, `react-dom` ^19.2, `vite` ^7.3, `typescript` ~5.9, `@tauri-apps/cli` ^2.10
- Map npm: `@xyflow/react` (graph rendering), `framer-motion` (panel animations), `d3-force` ^3.0 (force-directed graph layout), `react-router-dom` ^7.13 (client-side routing)
- Styling npm: `tailwindcss` ^3.4, `tailwindcss-animate`, `autoprefixer`, `postcss`, `tailwind-merge`, `clsx`, `class-variance-authority`
- UI npm: `lucide-react` (icons), `next-themes` (light/dark toggle), `sonner` (toast notifications), `@radix-ui/react-slot`, `@radix-ui/react-tabs`, `@radix-ui/react-scroll-area`, `@radix-ui/react-switch`, `@radix-ui/react-slider`, `@radix-ui/react-separator`, `@radix-ui/react-label`, `@radix-ui/react-dropdown-menu`, `@radix-ui/react-dialog`, `@radix-ui/react-tooltip`, `@radix-ui/react-select`
- Cargo: `tauri` 2.10, `serde`/`serde_json`, `log`, `tauri-plugin-log`

#### Design System (Tailwind CSS + HSL CSS variables)

Uses Tailwind CSS with HSL CSS variable tokens. Light/dark mode toggled via `.dark` class on `<html>` (managed by `next-themes`). All components use semantic Tailwind classes (`bg-background`, `text-primary`, `border-border`, etc.).

**Fonts:** Inter (UI text), Lora (headings), Space Mono (code/monospace)

**Light Theme** (warm cream / forest green):

| Token | HSL | Usage |
|-------|-----|-------|
| `--background` | 40 33% 96% | Page background |
| `--foreground` | 150 25% 20% | Primary text |
| `--primary` | 150 25% 28% | Buttons, accents, ASCII logo |
| `--card` | 40 30% 98% | Cards, panels, surfaces |
| `--muted` | 40 15% 92% | Dimmed backgrounds |
| `--muted-foreground` | 150 10% 45% | Secondary text |
| `--border` | 40 15% 88% | Borders, dividers |
| `--destructive` | 0 65% 50% | Error/delete actions |

**Dark Theme** (deep forest / bright green):

| Token | HSL | Usage |
|-------|-----|-------|
| `--background` | 150 20% 10% | Page background |
| `--foreground` | 40 30% 95% | Primary text |
| `--primary` | 150 30% 50% | Buttons, accents, ASCII logo |
| `--card` | 150 20% 14% | Cards, panels, surfaces |
| `--muted` | 150 15% 20% | Dimmed backgrounds |
| `--muted-foreground` | 150 10% 60% | Secondary text |
| `--border` | 150 10% 25% | Borders, dividers |
| `--destructive` | 0 60% 55% | Error/delete actions |

**Node Type Colors** (shared, both themes):

| Variable | HSL | Color |
|----------|-----|-------|
| `--node-component` | 150 40% 35% | Forest green |
| `--node-api` | 180 50% 35% | Cyan/teal |
| `--node-utility` | 35 70% 50% | Gold/amber |
| `--node-data` | 200 60% 45% | Sky blue |
| `--node-config` | 150 10% 50% | Gray-green |
| `--node-problem` | 0 65% 55% | Red |
| `--node-rust` | 15 70% 50% | Orange |
| `--node-actor` | 280 45% 55% | Purple |
| `--node-external` | 220 50% 50% | Blue |

---

### Key Technical Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Desktop framework | Tauri 2.10 (Rust) | Lightweight, secure, no Electron bloat |
| Graph rendering | @xyflow/react | Proven for architecture diagrams; custom nodes/edges; pan/zoom/minimap built-in |
| Two zoom levels | L2 (modules) + L3 (components) | Matches pipeline DB output; L1/L4 not produced by pipeline |
| Level switching | Replace all nodes, not nested expand | Simpler state; L2 and L3 have different edge sets |
| L2 layout | d3-force with degree-based centering + density-aware collide radius | Auto-positions modules; highly-connected nodes gravitate to center; busy modules get larger collision radius for edge breathing room |
| L3 layout | Hierarchical layered layout per module + d3-force group positioning | Phase 1: degree-based layered layout per module — BFS from highest-degree node assigns layers, barycenter heuristic orders within layers, disconnected components placed in grid below. Phase 2: d3-force positions module groups using cross-module edges, area-proportional repulsion, per-node collide radius. Phase 3: compose. Replaces force-directed per-module sim with structural hierarchy. |
| Edge rendering | Bezier curves | Clean curved paths between nodes; type-based coloring distinguishes dependency kinds |
| Edge styling | SVG-native (not EdgeLabelRenderer) | Better performance; transforms with zoom/pan automatically |
| Edge weight scaling | Logarithmic (1-5px) | Compresses extreme values; maintains visual hierarchy without overwhelming thick edges |
| Dynamic weight slider | Slider max computed from actual edge data | Replaces hardcoded max=10; adapts to real weight range per L2/L3 level |
| Normalized edge weights | Backend normalizes by geometric mean of component file counts | Makes weight represent coupling density, not raw reference counts; enables meaningful weight-based filtering |
| Detail panel | Slide-in from right, decisions grouped by category | Rich info without cluttering the graph; category grouping matches DB schema |
| Backend communication | Vite proxy to FastAPI | Same-origin; backend owns DB access and export |
| Data transform layer | Dedicated `mapTransform.ts` + `autoLayout.ts` | Separates DB shape from @xyflow shape; async layout computation; testable |
| State management | React useState | useState for UI (selected node, level, filters, search); async layout computed in useEffect |
| Styling | Tailwind CSS + shadcn/ui + CSS variables | Utility-first CSS with consistent design tokens; shadcn/ui provides accessible Radix UI-based components |
| Component library | shadcn/ui (Radix UI primitives) | Accessible, consistent, copy-paste components — Button, Dialog, Input, Select, Tabs, ScrollArea, etc. |
| Theme switching | next-themes with class-based dark mode | Light/dark toggle via `.dark` class on `<html>`; user preference persisted to localStorage |
| Typography | Inter (UI), Lora (headings), Space Mono (code) | Warm, readable font stack matching Context viewer design system |
| Toast notifications | Sonner | Non-blocking toast notifications; replaces `alert()` calls |

---

## Planned Changes

- [x] Add model dropdown per provider (replaces free-text model input)
- [x] Add repository path input field to launcher
- [x] Add `GET /api/map` endpoint to backend (calls `export_full_map()`)
- [x] Add `fetchMap()` to `api/client.ts`
- [x] Create `data/types.ts` with map TypeScript interfaces
- [x] Create `data/mapTransform.ts` (export_full_map JSON → @xyflow nodes/edges)
- [x] Install `@xyflow/react` and `framer-motion`
- [x] Build `MapView.tsx` — ReactFlow orchestrator with level state
- [x] Build `MapNode.tsx` — custom node (module at L2, component at L3)
- [x] Build `GroupNode.tsx` — module group backgrounds for L3
- [x] Build `AnimatedEdge.tsx` — weighted, typed edges
- [x] Build `DetailPanel.tsx` — decisions by category, metadata
- [x] Build `MapSidebar.tsx` — level toggle, edge filters
- [x] Add routing: `/` (launcher), `/map` (map view)
- [x] Add streaming output for pipeline launcher
- [x] Support running Parts 2 and 3 from the UI

### L2 Layout Fix: Isolated Modules

- [x] Fix edgeless modules drifting far from cluster — increase centering strength for degree-0 nodes in `autoLayout.ts`

### Map Editing (human edits → change records)

- [x] Add inline edit controls to `DetailPanel.tsx`
- [x] Add delete button (×) per decision in `DetailPanel.tsx`
- [x] Add "+ Add decision" button in `DetailPanel.tsx`
- [x] After each mutation, re-fetch `GET /api/change-records` and overlay +/~/- diff badges on decisions (green=add, amber=edit, red=remove)
- [x] Add `fetchChangeRecords()` to `api/client.ts`

### Ticket Generation (AI converts change records → tickets)

- [x] Add "Generate Tickets" button to `MapSidebar.tsx` with change count badge
- [x] Build `TicketPanel.tsx` — slides up from bottom of canvas, lists tickets, shows map correction count
- [x] Each ticket: title, description, acceptance criteria, affected files, "Copy" button (markdown), "Copy All" button
- [x] Add `generateTickets()` and `fetchTickets()` to `api/client.ts`
- [x] After ticket generation, change records refresh (new baseline resets diff badges)

---

### How-It-Works Steps Box on Launcher Page

- [x] Add a compact "how it works" description box between the version line and the form fields
- [x] Steps: 1. Set API key & repo path → 2. Run Parts 1-3 → 3. View Architecture Map → 4. Edit & generate tickets
- [x] Warning note: Re-running the pipeline resets the map — generate tickets first to keep your changes

### External Dependency Label on Nodes

- [x] Module nodes (L2): if `directories` is empty, show "External dependency" badge below the meta row
- [x] Component nodes (L3): if `files` is empty, show "External dependency" badge below the meta row
- [x] Badge styled with dim amber color to distinguish from classification badges

### Node & Edge Creation from Map UI

- [x] Add 4 new POST endpoints: `/api/modules`, `/api/components`, `/api/module-edges`, `/api/component-edges`
- [x] Add `createModule()`, `createComponent()`, `createModuleEdge()`, `createComponentEdge()` to `api/client.ts`
- [x] Add edge type constants (`MODULE_EDGE_TYPES`, `COMPONENT_EDGE_TYPES`) to `data/types.ts`
- [x] Build `CreateNodeModal.tsx` — modal for creating modules (L2) or components (L3)
- [x] Build `EdgeLabelPopup.tsx` — modal for edge type + label after handle drag
- [x] Add "+ Module/Component" button to map topbar
- [x] Wire `onConnect` + `isValidConnection` into ReactFlow for edge dragging
- [x] Add `refreshMap()` callback to reload graph after create operations
- [x] Add modal styles (shadcn Dialog components)

### Green Highlight on Nodes with Pending Changes

- [x] DB schema: Add `module_id` and `component_id` columns to `change_records` table (`db.py`)
- [x] `add_change_record()`: Accept `module_id`/`component_id` params, store directly on row
- [x] All write endpoints (decision CRUD, module/component create/delete): pass context at write time
- [x] `_migrate()`: Auto-add columns to older databases via `ALTER TABLE`
- [x] `GET /api/change-records`: Read context directly from row — no enrichment queries needed
- [x] Ticket generation: Same simplification — context read from row
- [x] Frontend: `changedNodeIds` derived from `changeRecords`, `hasChanges` injected into node data
- [x] MapNode: `ring-2 ring-amber-500` Tailwind classes for `.has-changes` state

### Re-validation & Map Versioning

- [x] Add `ValidationSummary`, `DecisionValidation`, `MapVersion`, `VersionDecision`, `VersionComparison`, `ValidationRun` types to `types.ts`
- [x] Add 7 new API client functions for versions and validation to `client.ts`
- [x] Add purple `.has-revalidation` highlighting (ring-2 ring-blue-500 Tailwind classes)
- [x] Add validation badge styles (updated=blue, outdated=red, new=purple, implemented=green, diverged=amber — Tailwind utility classes)
- [x] Update `MapNode.tsx` with `hasRevalidation` flag
- [x] Update `MapView.tsx` with `validationSummary` state, `revalidatedNodeIds`, `styledNodes` injection, VersionPanel wiring
- [x] Update `DetailPanel.tsx` with validation badges per decision (status label + hover tooltip with reason + old text strikethrough for updated)
- [x] Build `VersionPanel.tsx` — bottom slide-up with version list/detail/comparison views
- [x] Add History section to `MapSidebar.tsx` (Browse Versions + Save Snapshot buttons)
- [x] Add "Re-validate" step to `App.tsx` STEPS array

### Easier Edge Creation (Handle UX)

- [x] Increase handle size from 20px to 80px so they're easier to grab at any zoom level
- [x] Add `connectionRadius={200}` to ReactFlow so dropping near a handle snaps to it (no pixel-perfect aim needed)
- [x] Add pulsing glow animation on handles so users can see connection points
- [x] Style the connecting line to be thicker and more visible during drag

### Export Map JSON

- [x] Add `exportMapAsFile()` to `api/client.ts` — fetches map data, wraps with version/timestamp, triggers browser download as `legend-map-{timestamp}.json`
- [x] Add `importMap()` to `api/client.ts` — POSTs JSON to `POST /api/map/import`
- [x] Add "Export Map JSON" button to `MapSidebar.tsx` with loading state
- [x] Wire `handleExportMap` callback and `exporting` state in `MapView.tsx`
- [x] Add `.sidebar-btn-export` CSS (green accent)
- [x] Backend: `POST /api/map/import` endpoint in `main.py` — accepts raw or wrapped format, calls `db.import_full_map()`
- [x] Backend: `import_full_map(conn, data)` in `db.py` — clears existing data, rebuilds with ID remapping

### Full Pipeline Run (Parts 1–3 Sequential)

- [x] Add `"full"` option to STEPS array in `App.tsx` (first in list, label: "Full Pipeline (Parts 1–3)")
- [x] Add `pipelineStep` state (0 = idle, 1–3 = current step number) and `pipelineCompleted` state (completed step indices)
- [x] Add `PIPELINE_STEPS` constant mapping step names to labels
- [x] Add `runSingleStep()` helper: wraps `runOpenCodeStream` in a Promise that resolves to success boolean
- [x] Modify `handleRun`: when `step === "full"`, loop through `["part1","part2","part3"]` calling `runSingleStep` for each sequentially
- [x] Insert separator lines in output between steps (e.g. `── Step 2/3: Component Discovery ──`)
- [x] Stop pipeline if any step fails (don't proceed to next)
- [x] Show 3-segment pipeline progress indicator above output block: pending (dim), active (cyan blink), completed (green)
- [x] Update output header to show "running step N/3 — {stepName}" during full pipeline
- [x] Run button text: "$ run full pipeline" when idle, "executing... (step N/3)" when running
- [x] Cancel button aborts current step and stops entire pipeline
- [x] Existing individual step behavior unchanged
- [x] Pipeline progress CSS: `.pipeline-progress`, `.pipeline-segment` (active/completed/pending), `.pipeline-dot`, `.pipeline-label`

### Per-Module Simulation Layout for L3 + L2 Density Spacing

Replaces single global d3-force simulation with per-module simulations for better spacing in busy modules.

**L3 — 3-phase pipeline in `mapTransform.ts` `buildL3()`:**
- [x] Phase 1: Per-module internal layout — isolated d3-force sim per module, adaptive link distance + repulsion based on intra-module edge density
- [x] Phase 2: Module group positioning — second d3-force sim treating each module as rectangular node, cross-module edges as links, area-proportional repulsion, per-node collide radius from bounding box
- [x] Phase 3: Compose — combine relative component positions with group positions into final absolute positions
- [x] Edge-scaled GROUP_PADDING driven by intra-module edge count: `GROUP_PADDING * (1 + 0.15 * min(intraEdges, 20))`

**L2 — density-aware spacing in `autoLayout.ts`:**
- [x] Replace fixed `COLLIDE_RADIUS` with per-node function: `COLLIDE_RADIUS * (1 + 0.02 * min(degree, 20))`

---

### L3 Hierarchical Layout + Dynamic Weight Slider

- [x] Replace `layoutModule()` force-directed sim with degree-based hierarchical layered layout (BFS from core, barycenter ordering, grid for disconnected)
- [x] Add `getWeightRange()` helper for dynamic slider bounds
- [x] Update `MapSidebar` weight slider: dynamic max, adaptive step
- [x] Pass `weightRange` from `MapView` to `MapSidebar`

---

## Log

- 2026-02-16 :: william :: Created doc (Section 1 + Section 2 draft)
- 2026-02-16 :: william :: NLC audit fix: Added classification to module data model, updated edge format to split module_edges/component_edges, added purpose/confidence to components
- 2026-02-16 :: william :: Implemented enhanced edge display: smooth step paths, type-based colors (call=cyan, import=purple, inheritance=green), logarithmic weight scaling, animated dots, search dimming, selection highlighting, SVG-native rendering
- 2026-02-16 :: william :: Implemented automatic module layout: ELK hierarchical layout algorithm, classification-based grouping, dependency-aware positioning
- 2026-02-16 :: william :: Added map editing and ticket generation to Planned Changes (moved out of Long Term). Added TicketPanel.tsx to key components. Updated api/client.ts function list.
- 2026-02-16 :: william :: Implemented full map editing + ticket generation frontend. DetailPanel: diff badges (+/~/- with green/amber/red), onMutate refresh. MapSidebar: Generate Tickets button with change count badge. TicketPanel: bottom-sliding panel, copy-per-ticket + copy-all. MapView: changeRecords state, refreshChangeRecords, handleGenerateTickets (reads apiKey from localStorage). Types: ChangeRecord, GeneratedTicket, SavedTicket, TicketGenerateResponse. CSS: diff-badge, ticket-panel, ticket-card, sidebar-tickets.
- 2026-02-17 :: william :: Replaced free-text model input with provider-specific model dropdown. PROVIDER_MODELS map defines models per provider (anthropic: 3 models, openai: 3, google: 2, groq: 3). Switching provider auto-selects first model. Selection persisted to localStorage.
- 2026-02-17 :: william :: Fixed isolated modules drifting far from cluster in L2 map view. Degree-0 nodes now get centering strength 0.12 (was 0.02) so they stay near the connected modules instead of being pushed to the periphery by repulsion. Also corrected doc: layout is d3-force, not ELK.
- 2026-02-17 :: william :: Fixed Google model prefix from google/ to gemini/ (litellm routing format for Google AI Studio).
- 2026-02-18 :: william :: Doc sync: Fixed layout description (ELK→d3-force), edge rendering (smooth step→Bezier, removed animated dots), updated dependencies (added d3-force, react-router-dom, removed elkjs), fixed API function names (patchDecision→updateDecision, added runOpenCodeStream), updated How it Works for streaming, marked all implemented Planned Changes as complete.
- 2026-02-19 :: william :: Added "how it works" steps box to launcher page: 4-step flow (Set API key & repo → Run Parts 1-3 → View Map → Edit & generate tickets) with amber warning about pipeline re-run resetting the map.
- 2026-02-19 :: william :: Implemented node + edge creation from map UI. Backend: 4 new POST endpoints (modules, components, module-edges, component-edges) with IntegrityError→409 for duplicate edges. Frontend: CreateNodeModal (L2 module / L3 component forms), EdgeLabelPopup (edge type dropdown + label after handle drag), "+ Module/Component" topbar button, refreshMap callback, onConnect/isValidConnection wiring. New files: CreateNodeModal.tsx, EdgeLabelPopup.tsx. Edge type constants added to types.ts.
- 2026-02-19 :: william :: Added "External dependency" label to map nodes. Module nodes with empty directories array and component nodes with empty files array now display an amber "External dependency" label below the meta row.
- 2026-02-19 :: william :: Green highlight on nodes with pending decision changes. Backend returns component_id/module_id in change record context. MapView derives changedNodeIds from changeRecords and injects hasChanges flag into node data. MapNode applies has-changes CSS class for green border + glow. Highlight clears automatically when tickets are generated (baseline advances, changeRecords empty).
- 2026-02-19 :: william :: Made edge creation much easier. Handles enlarged from 20px to 80px with pulsing cyan glow animation. Added connectionRadius={200} to ReactFlow so dropping near a handle snaps to it. Connection line styled thicker (8px cyan) during drag. No more pixel-perfect clicking needed.
- 2026-02-19 :: william :: Added Export Map JSON feature. Frontend: "Export Map JSON" button in sidebar (green accent) downloads full map as `legend-map-{timestamp}.json` with version wrapper. Backend: `POST /api/map/import` endpoint accepts exported JSON (raw or wrapped format) and calls `db.import_full_map()` which clears existing data and rebuilds with ID remapping. Round-trip capable: export → import preserves all modules, components, decisions, and edges.
- 2026-02-19 :: william :: Cleaned up change record context: added `module_id`/`component_id` columns directly to `change_records` table. All write endpoints now store context at insert time. Removed all read-time enrichment queries from GET /api/change-records and ticket generation. Added `_migrate()` for older DBs. CSS green highlight scaled up to 30px border + background tint for visibility at graph zoom levels. Module/component creation now also creates change records for green highlight.
- 2026-02-19 :: william :: Implemented re-validation & map versioning frontend. Types: 6 new interfaces (MapVersion, VersionDecision, VersionComparison, DecisionValidation, ValidationSummary, ValidationRun). API: 7 new client functions. Purple highlighting: has-revalidation CSS (30px purple border + glow, gradient split with green when both changes and revalidation), MapNode hasRevalidation flag, MapView validationSummary state + revalidatedNodeIds. DetailPanel: validation badges per decision (updated/outdated/new/implemented/diverged) with hover tooltip for LLM reason, old text strikethrough for updated decisions. VersionPanel.tsx: bottom slide-up panel with 3 views (version list with compare dropdowns, version detail grouped by module, version comparison with added/removed/changed diff). MapSidebar: History section with Browse Versions and Save Snapshot buttons. App.tsx: "Re-validate" step added to launcher STEPS.
- 2026-02-20 :: william :: Added "Full Pipeline" run option to launcher. New "Full Pipeline (Parts 1–3)" option (first in step dropdown, new default) chains part1→part2→part3 sequentially via frontend orchestration (no backend changes). Shows 3-segment progress indicator (pending/active/completed) above output. Output header shows "running step N/3 — {label}". Separators between steps in output stream. Stops on first failure. Cancel aborts current step and halts pipeline. Individual steps still work as before. Files: App.tsx (PIPELINE_STEPS const, runSingleStep helper, handleRun full-pipeline branch, pipelineStep/pipelineCompleted state, progress UI), App.css (pipeline-progress, pipeline-segment, pipeline-dot styles).
- 2026-02-20 :: william :: Rewrote L3 layout as per-module simulation pipeline. Replaced single global d3-force simulation + clustering force + pairwise overlap resolution with 3-phase approach: Phase 1 runs isolated d3-force per module with adaptive parameters (link distance 1920→3200 and repulsion 1x→1.5x based on intra-module edge density). Phase 2 positions module groups via second simulation (area-proportional repulsion, per-node collide radius from bounding box, cross-module + module edges as links). Phase 3 composes final positions. GROUP_PADDING scales by intra-module edge count. L2 layout updated with density-aware collide radius (busy modules get 10-40% larger). Files: mapTransform.ts (buildL3 rewrite, layoutModule, layoutModuleGroups), autoLayout.ts (per-node collide radius).
- 2026-02-20 :: william :: Replaced per-module d3-force layout with hierarchical layered layout for L3 view. layoutModule() now uses degree-based BFS layering: highest-degree component at top (layer 0), neighbors at layer 1, etc. Barycenter heuristic orders nodes within layers to minimize edge crossings (two passes: top-down + bottom-up). Disconnected components placed in grid below hierarchy. Multiple connected subgraphs laid out side-by-side. Module group positioning (Phase 2) still uses d3-force. Also: dynamic weight slider (max computed from actual edge data via getWeightRange()), MapSidebar shows "min / max" values. Files: mapTransform.ts (layoutModule rewrite, getWeightRange), MapSidebar.tsx (weightRange prop), MapView.tsx (weightRange memo + prop pass).
- 2026-02-20 :: william :: Performance fixes + Tauri download fix. (1) Reduced box-shadow blur from 200px/80px to 15px/6px on .has-changes and .has-revalidation nodes in graph.css — WKWebView was GPU-thrashing on large blurs. (2) Stopped infinite CSS handle-pulse animation on all 8 handles per node (moved to :hover only). (3) Added React.memo() to MapNode, AnimatedEdge, GroupNode to prevent unnecessary re-renders. (4) Fixed Tauri file download: blob URL downloads don't work in WKWebView, replaced with Tauri dialog+fs plugins (save dialog + writeTextFile) for both ticket download and map export. Added tauri-plugin-dialog and tauri-plugin-fs to Cargo.toml, lib.rs, capabilities. (5) Reduced d3-force ticks: autoLayout 400→200, mapTransform 300→150. (6) Raised ReactFlow minZoom from 0.001 to 0.1. (7) Tuned framer-motion springs: damping 25-28→40, stiffness 300→200 for faster settling in DetailPanel, TicketPanel, VersionPanel.
- 2026-02-28 :: william :: Frontend theme overhaul: migrated from plain CSS to Tailwind CSS + shadcn/ui. Adopted Context viewer design system with dual light/dark themes (warm cream/forest green light, deep forest/bright green dark). Added next-themes toggle, sonner toasts, lucide-react icons. Replaced App.css and graph.css (~1800 lines) with Tailwind utility classes across all 11 graph components + launcher. New files: tailwind.config.ts, postcss.config.js, components.json, lib/utils.ts, ThemeToggle.tsx, 16 shadcn/ui components. Fonts: Inter (UI), Lora (headings), Space Mono (code). All component logic unchanged.
