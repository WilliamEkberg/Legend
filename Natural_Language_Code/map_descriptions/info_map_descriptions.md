# Map Descriptions

> Part of [Legend Architecture Map](../legend_map/info_legend_map.md)

## Section 1 (HUMAN EDITS ONLY)

### Purpose

Generate human-readable descriptions for every component and module in the architecture map. Descriptions are structured as technical decisions — concrete, falsifiable statements about what the code does and why — not prose summaries. The result is an architecture map where each element carries the specific constraints that define it.

---

### How it Works

#### The "Technical Decision as Constraint" Model

A technical decision is a concrete statement that constrains the solution space, like a differential equation constraint bounds possible solutions. Good technical decisions are:

- **Falsifiable** — You can check the code and confirm or deny the statement. "Uses PostgreSQL for persistence" is falsifiable. "Has a good database layer" is not.
- **Specific** — Names technologies, patterns, boundaries, and strategies. "Rate limits API calls to 100/min using a token bucket" vs "handles rate limiting."
- **Structural** — Captures the choices that shape the code, not what the code does line-by-line. API contracts, error handling strategies, library choices, architectural patterns, integration boundaries.

Together, a component's technical decisions define what that component is — change a decision and you change the component. A module's decisions are the union and elevation of its components' decisions, plus deployment-level concerns.

#### Workflow

1. **Component descriptions first (bottom-up)** — Read each component's source files and extract technical decisions. See [Component Descriptions](./info_map_descriptions_component.md).

2. **Module descriptions second (aggregation)** — Collect component decisions, identify cross-cutting patterns, deduplicate by elevating shared decisions to the module level, and add deployment-level concerns. See [Module Descriptions](./info_map_descriptions_module.md).

3. **Attach to map** — Store decisions as structured data (list items per component/module) in the architecture map output.

#### Rules & Edge Cases

- Descriptions are generated from source code, not from comments or documentation — the code is the source of truth
- If a component has too few files to extract meaningful decisions, it gets a minimal description with just its file list and primary responsibility
- Conflicting decisions between components (e.g., two components using different serialization formats) are kept as-is — they represent real architectural inconsistency worth surfacing

---

## Sub-documents

- [Component Descriptions (Part 3a)](./info_map_descriptions_component.md)
- [Module Descriptions (Part 3b)](./info_map_descriptions_module.md)

---

## Section 2 (HUMAN + AI)

### Architecture

#### Data Model

**Input (from DB):**
```
modules         — id, name, type, technology, source_origin, deployment_target
components      — id, module_id, name
component_files — component_id, path, is_test
```

**Output (written to DB):**
```
decisions       — id, module_id|component_id, category, text, source, pipeline_run_id
```

**Intermediate structures:**
```
ComponentDecisionBatch {
    component_id:   int
    component_name: str
    decisions:      list[{category: str, text: str}]
}

ModuleElevationResult {
    module_id:          int
    elevated_decisions: list[{category: 'cross_cutting', text: str}]
    deleted_ids:        list[int]          -- component decision IDs to remove
    deployment_decisions: list[{category: 'deployment', text: str}]
}
```

#### System Flow

```
Phase A — Component Descriptions
──────────────────────────────────
For each component:
    DB: get_component_files(component_id, exclude_test=True)
    Disk: read source files
    LLM: extract decisions (3-15 per component)
    DB: add_decision(component_id=..., category, text)

         │
         v

Phase B — Module Descriptions
──────────────────────────────────
For each module:
    DB: get_components(module_id)
    DB: get_decisions(component_id=...) for each component
    LLM: elevation call — identify cross-cutting decisions
    DB: delete_decisions_by_ids(elevated originals)
    DB: add_decision(module_id=..., category='cross_cutting', text)
    Disk: find deployment files (Dockerfile, package.json, etc.)
    LLM: deployment call — extract deployment decisions
    DB: add_decision(module_id=..., category='deployment', text)
```

#### Key Components

| File | Responsibility |
|------|----------------|
| `backend/map_descriptions/pipeline.py` | Entry point, orchestrates Phase A then Phase B |
| `backend/map_descriptions/component_describer.py` | Reads source files, calls LLM, writes component decisions |
| `backend/map_descriptions/module_describer.py` | Collects component decisions, runs elevation + deployment LLM calls |
| `backend/map_descriptions/prompts.py` | All LLM prompt templates (component, elevation, deployment) |
| `backend/map_descriptions/llm_client.py` | LLM API wrapper (copied from Part 2 pattern, consolidate later) |

---

### Implementation

#### Entry Point

```python
run_descriptions_pipeline(db_path: str, source_dir: str, model: str) -> None
```

Orchestrates the full Part 3 run:
1. `connect(db_path)`, `start_pipeline_run(conn, 'part3')`
2. On re-run: `clear_decisions(conn, source='pipeline_generated')`
3. Phase A: `describe_all_components(conn, source_dir, client, run_id)`
4. Phase B: `describe_all_modules(conn, source_dir, client, run_id)`
5. `complete_pipeline_run(conn, run_id)`

#### DB Functions Used

**Read:**
- `get_modules(conn)` — list all modules
- `get_components(conn, module_id)` — list components in a module
- `get_component_files(conn, component_id, exclude_test=True)` — file paths for a component
- `get_decisions(conn, component_id=...)` — component decisions (for elevation input)

**Write:**
- `add_decision(conn, category, text, component_id=..., source='pipeline_generated', run_id=...)`
- `add_decision(conn, category, text, module_id=..., source='pipeline_generated', run_id=...)`

**Delete:**
- `clear_decisions(conn, source='pipeline_generated')` — wipe pipeline decisions on re-run
- `delete_decisions_by_ids(conn, ids: list[int])` — remove elevated component decisions

**Pipeline:**
- `start_pipeline_run(conn, 'part3')` / `complete_pipeline_run(conn, run_id)`

#### New DB Function Needed

```python
delete_decisions_by_ids(conn, ids: list[int]) -> None
```

Deletes specific decisions by their primary key. Used by the elevation step to remove component-level decisions that have been promoted to module level.

---

### Key Technical Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Subdirectory layout | `backend/map_descriptions/` mirrors NLC folder | Keeps Part 3 code isolated; consistent with project structure |
| Two-phase pipeline | Phase A (components) then Phase B (modules) | Module descriptions depend on component decisions existing in DB |
| Source code as LLM input | Send actual source files, not SCIP metadata | Decisions describe what the code does; SCIP captures structure, not behavior |
| DB as inter-phase bridge | Phase B reads component decisions from DB | No in-memory coupling between phases; re-runnable independently |
| Re-run strategy | `clear_decisions(source='pipeline_generated')` first | Preserves user-edited decisions; replaces only pipeline output |
| LLM client | Copy from Part 2 pattern | Avoid premature abstraction; consolidate into shared utility later |
| User edits use `source='human'` | Decisions edited/added by user get a distinct source value | Allows pipeline re-runs to safely overwrite pipeline_generated while preserving user changes |
| Decision edit state in mapData | Edits propagate up via `onDecisionChange` callback to MapView, which updates both `mapData` and `selectedNode` | Prevents stale panel data on re-click without requiring a full data re-fetch |

---

## Planned Changes

- [x] Create `backend/map_descriptions/` package (`__init__.py`, `pipeline.py`)
- [x] Implement `component_describer.py` (Phase A)
- [x] Implement `module_describer.py` (Phase B)
- [x] Write prompt templates in `prompts.py`
- [x] Add `llm_client.py` (copy from Part 2)
- [x] Add `delete_decisions_by_ids` to `backend/db.py`
- [x] Add `get_component_files(exclude_test)` filter to `backend/db.py`

### Editable Decisions UI

- [x] Add `PATCH /api/decisions/{id}` endpoint (update text/category, set `source='human'`)
- [x] Add `POST /api/decisions` endpoint (create new decision for module or component)
- [x] Add `DELETE /api/decisions/{id}` endpoint
- [x] Add `updateDecision`, `createDecision`, `deleteDecision` to `frontend/src/api/client.ts`
- [x] Update `MapView.tsx` — add `handleDecisionChange` callback, pass to `DetailPanel`
- [x] Update `DetailPanel.tsx` — inline editing, add/delete, green highlight for `human`
- [x] Update `graph.css` — styles for edit textarea, save/cancel buttons, green highlight, add form

---

## Log

- 2026-02-16 :: william :: Created doc (Section 1 draft)
- 2026-02-16 :: william :: Filled Section 2 (architecture, implementation, decisions)
- 2026-02-16 :: william :: Implemented full backend/map_descriptions package (all planned changes complete)
- 2026-02-16 :: william :: Added editable decisions UI — inline edit/add/delete in DetailPanel, PATCH/POST/DELETE API endpoints, green highlight for human decisions, decision edits propagate to mapData via onDecisionChange callback
- 2026-02-16 :: william :: Bug fix — decision endpoints now raise HTTPException instead of returning {"id": null} on DB not found; DetailPanel add/edit/delete handlers now catch errors and display them inline
- 2026-02-17 :: william :: Migrated llm_client.py from anthropic SDK to litellm. Removed _strip_provider_prefix from pipeline.py — litellm accepts full provider/model strings directly.
- 2026-02-18 :: william :: Doc sync: Changed source='user_edited' to source='human' throughout to match actual implementation in main.py.
