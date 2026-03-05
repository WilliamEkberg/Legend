# Database

> Part of [Legend Architecture Map](../legend_map/info_legend_map.md)

## Section 1 (HUMAN EDITS ONLY)

### Purpose

Provide a single persistent store for the entire Legend pipeline. Instead of each step producing loose JSON files in `output/`, every pipeline step reads from and writes to one SQLite database per analyzed repository. The database is the source of truth for modules, components, edges, technical decisions, change records, baselines, and implementation tickets.

---

### How it Works

#### One Database Per Repository

Each analyzed repository gets its own SQLite file (e.g. `legend_<repo>.db`). There is no multi-repo mode — one DB, one repo. No server process required; SQLite is file-based and embedded.

#### Pipeline Steps Write to It

1. **Part 1 (opencode_runner)** — Writes modules and their directory mappings. Each module has a name, type, technology, source origin, deployment target, and **classification** (`module`, `shared-library`, or `supporting-asset`). All entity types (modules, shared libraries, supporting assets) are stored as rows in the `modules` table, distinguished by the `classification` column. Part 1 also writes L2 edges from its relationship data. Directories are stored as a join table, not a JSON array.

2. **Part 2 (component_discovery)** — Writes components within each module and their file mappings. Each component includes a `purpose` (text describing its responsibility) and `confidence` (0.0–1.0 from LLM refinement). Writes L3 edges (component-to-component) only. L2 edges are written by Part 1, not Part 2.

3. **Part 3 (map_descriptions)** — Writes technical decisions for each component and module. Decisions use a polymorphic foreign key — each decision points to either a module OR a component (never both, enforced by a CHECK constraint). Decisions are categorized using a fixed set: `api_contracts`, `patterns`, `libraries`, `boundaries`, `error_handling`, `data_flow`, `deployment`, `cross_cutting`.

4. **Research agent** — Reads the current map. Humans edit the map directly (CRUD on decisions and components). Every edit creates a **change record** tracking what changed, old/new values, origin (human/ai), and which baseline it belongs to. V1 = map editing + change tracking + review UI. AI suggestions = V2 (future).

5. **Ticket generation** — Reads change records since the last baseline and generates implementation tickets. Each ticket links to its source change records (traceability) and lists affected files. A new baseline is created after ticket generation.

#### A Single Python Module Owns All Access

All database interaction goes through `backend/db.py`. No raw SQL outside this module. Pipeline steps call functions like `add_module()`, `add_decision()`, `add_change_record()` — they never construct queries themselves.

#### Re-run Strategy

When the pipeline is re-run on the same repository:

- **Part 1 re-run = full rebuild.** Call `create_baseline()` before `clear_modules()` to fence off old change records. Then delete and recreate all modules. `ON DELETE CASCADE` handles cleanup — deleting a module cascades to its directories, components, files, module_edges, component_edges, and pipeline-generated decisions. **All human edits are lost.** The frontend should show a confirmation warning before triggering a Part 1 re-run. A new baseline is created after the rebuild completes.
- **Part 3 re-run = safe.** `clear_decisions(source='pipeline_generated')` preserves human decisions (because the research agent sets `source='human'` when a human edits a pipeline-generated decision). Only pipeline-generated decisions are replaced.
- Change records and baselines are never deleted — they form the permanent audit trail regardless of re-runs

#### Rules & Edge Cases

- A module with no source files (config-only) skips Part 2 — it gets decisions directly from Part 3
- If the database file does not exist, `init_schema()` creates it with all tables
- If it already exists, the pipeline can clear and rebuild specific entities without dropping the schema
- The research agent can run independently of the pipeline — it only needs the map to exist
- Shared libraries and supporting assets are stored as module rows with `classification` set accordingly — no separate tables

---

## Section 2 (HUMAN + AI)

### Architecture

#### Schema (14 tables)

```sql
-- Track pipeline execution history
CREATE TABLE pipeline_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    step        TEXT NOT NULL,           -- 'part1', 'part2', 'part3', 'research_agent', 'ticket_generation'
    started_at  TEXT NOT NULL,           -- ISO 8601
    completed_at TEXT,                   -- NULL while running
    status      TEXT NOT NULL DEFAULT 'running',  -- 'running', 'completed', 'failed'
    metadata    TEXT                     -- JSON blob for step-specific info
);

-- L2 modules, shared libraries, and supporting assets (from Part 1)
CREATE TABLE modules (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL UNIQUE,
    classification    TEXT NOT NULL DEFAULT 'module',  -- 'module', 'shared-library', 'supporting-asset'
    type              TEXT,              -- 'web-application', 'api-service', 'cli-tool', etc.
    technology        TEXT,              -- 'next.js', 'fastapi', 'go', etc.
    source_origin     TEXT,              -- 'in-repo', 'external', 'managed-service'
    deployment_target TEXT,              -- 'docker', 'kubernetes', 'vercel', etc.
    pipeline_run_id   INTEGER REFERENCES pipeline_runs(id),
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Directory-to-module mapping (join table)
CREATE TABLE module_directories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    module_id   INTEGER NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
    path        TEXT NOT NULL,
    UNIQUE(module_id, path)
);

-- L3 components within modules (from Part 2)
CREATE TABLE components (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    module_id       INTEGER NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    purpose         TEXT,                -- component responsibility description (from LLM refinement)
    confidence      REAL,                -- 0.0-1.0 clustering/LLM confidence score
    pipeline_run_id INTEGER REFERENCES pipeline_runs(id),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(module_id, name)
);

-- File-to-component mapping (join table)
CREATE TABLE component_files (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    component_id    INTEGER NOT NULL REFERENCES components(id) ON DELETE CASCADE,
    path            TEXT NOT NULL,
    is_test         INTEGER NOT NULL DEFAULT 0,  -- 1 for test files assigned to this component
    UNIQUE(component_id, path)
);

-- L2 module-to-module dependencies (from Part 1)
CREATE TABLE module_edges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id   INTEGER NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
    target_id   INTEGER NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
    edge_type   TEXT,                    -- 'depends_on', 'uses_data_store', 'communicates_via'
    weight      REAL DEFAULT 1.0,
    metadata    TEXT,                    -- JSON blob for extra info (protocol, description)
    pipeline_run_id INTEGER REFERENCES pipeline_runs(id),
    UNIQUE(source_id, target_id, edge_type)
);

-- L3 component-to-component dependencies (from Part 2)
CREATE TABLE component_edges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id   INTEGER NOT NULL REFERENCES components(id) ON DELETE CASCADE,
    target_id   INTEGER NOT NULL REFERENCES components(id) ON DELETE CASCADE,
    edge_type   TEXT,                    -- 'depends-on' (single combined edge per pair)
    weight      REAL DEFAULT 1.0,
    metadata    TEXT,                    -- JSON: {"label": "..."}
    pipeline_run_id INTEGER REFERENCES pipeline_runs(id),
    UNIQUE(source_id, target_id)
);

-- Technical decisions — polymorphic FK to module OR component
CREATE TABLE decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    module_id       INTEGER REFERENCES modules(id) ON DELETE CASCADE,
    component_id    INTEGER REFERENCES components(id) ON DELETE CASCADE,
    category        TEXT NOT NULL,       -- 'api_contracts', 'patterns', 'libraries', 'boundaries',
                                         -- 'error_handling', 'data_flow', 'deployment', 'cross_cutting'
    text            TEXT NOT NULL,        -- Concise one-sentence decision summary
    detail          TEXT,                 -- Optional deeper context (why, how, alternatives)
    source          TEXT NOT NULL DEFAULT 'pipeline_generated',  -- 'pipeline_generated' or 'human'
    pipeline_run_id INTEGER REFERENCES pipeline_runs(id),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK ((module_id IS NOT NULL AND component_id IS NULL) OR
           (module_id IS NULL AND component_id IS NOT NULL))
);

-- Change records — tracks every map edit (decisions and components)
CREATE TABLE change_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type     TEXT NOT NULL,       -- 'decision' or 'component'
    entity_id       INTEGER NOT NULL,    -- decisions.id or components.id
    action          TEXT NOT NULL,        -- 'add', 'edit', 'remove'
    old_value       TEXT,                -- JSON: previous state (NULL for 'add')
    new_value       TEXT,                -- JSON: new state (NULL for 'remove')
    origin          TEXT NOT NULL,        -- 'human', 'chat', or 'ai'
    baseline_id     INTEGER REFERENCES baselines(id),
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Baselines — marks ticket generation boundaries
CREATE TABLE baselines (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_run_id INTEGER REFERENCES pipeline_runs(id),
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Implementation tickets from change records
CREATE TABLE tickets (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    title               TEXT NOT NULL,
    description         TEXT NOT NULL,
    acceptance_criteria TEXT,            -- testable conditions for "done"
    status              TEXT NOT NULL DEFAULT 'open',  -- 'open', 'in_progress', 'done'
    pipeline_run_id     INTEGER REFERENCES pipeline_runs(id),
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Files affected by a ticket
CREATE TABLE ticket_files (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    path        TEXT NOT NULL,
    UNIQUE(ticket_id, path)
);

-- Change-record-to-ticket traceability
CREATE TABLE ticket_change_records (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id           INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    change_record_id    INTEGER NOT NULL REFERENCES change_records(id) ON DELETE CASCADE,
    UNIQUE(ticket_id, change_record_id)
);

-- Decision-to-ticket traceability (direct link to source decisions)
CREATE TABLE ticket_decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    decision_id INTEGER NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
    UNIQUE(ticket_id, decision_id)
);
```

#### System Flow

```
Part 1 (opencode_runner)
    │  add_module(classification=...), add_module_directories(), add_module_edge(...)
    v
┌──────────────────────────────────┐
│          SQLite Database          │
│                                  │
│  modules ◄──── module_directories│
│     │                            │
│     ├──► module_edges            │
│     v                            │
│  components ◄── component_files  │
│     │                            │
│     └──► component_edges         │
│                                  │
│  decisions ──► change_records    │
│     │              │             │
│     v              v             │
│  ticket_decisions  baselines     │
│     │              │             │
│     v              v             │
│          tickets                 │
│          ├── ticket_files        │
│          └── ticket_change_      │
│              records             │
│                                  │
│  pipeline_runs (audit)           │
└──────────────────────────────────┘
    ^         ^         ^         ^
    │         │         │         │
  Part 2    Part 3   Research   Ticket
  (L3 edges)         Agent      Gen.
```

#### Pipeline Step Interaction

**Part 1 — Module Identification:**
```
start_pipeline_run('part1')
  For each discovered entity (module, shared library, or supporting asset):
    add_module(name, classification, type, technology, source_origin, deployment_target)
    add_module_directories(module_id, [dir_paths])
  For each relationship discovered:
    add_module_edge(source_module_id, target_module_id, edge_type, weight, metadata)
complete_pipeline_run(run_id)
```

**Part 2 — Component Discovery:**
```
start_pipeline_run('part2')
  For each module (with source code, classification != 'supporting-asset', source_origin == 'in-repo'):
    For each discovered component:
      add_component(module_id, name, purpose, confidence)
      add_component_files(component_id, [file_paths], [is_test])
    For each L3 edge:
      add_component_edge(source_component_id, target_component_id, edge_type, weight)
complete_pipeline_run(run_id)
```

**Part 3 — Description Generation:**
```
start_pipeline_run('part3')
  For each component:
    For each extracted decision:
      add_decision(component_id=id, category, text, source='pipeline_generated')
  For each module:
    For each elevated/deployment decision:
      add_decision(module_id=id, category, text, source='pipeline_generated')
  create_baseline(run_id)  -- initial baseline marks starting point for change tracking
complete_pipeline_run(run_id)
```

**Research Agent (V1 — Map Editing + Change Tracking):**
```
  get_modules(), get_components(), get_decisions()  -- read current map

  -- Human edits map in UI --
  For each edit:
    (CRUD operation on decision or component)
    add_change_record(entity_type, entity_id, action, old_value, new_value, origin='human')

  -- Review UI shows accumulated changes since last baseline --
  get_change_records(since_baseline_id)
```

**Ticket Generation:**
```
start_pipeline_run('ticket_generation')
  get_current_baseline()
  get_change_records(since_baseline_id)
  -- Net-delta collapsing, classification, grouping --
  For each ticket (or group of related changes):
    add_ticket(title, description, acceptance_criteria)
    add_ticket_files(ticket_id, [file_paths])
    link_ticket_change_records(ticket_id, [change_record_ids])
  create_baseline(run_id)
complete_pipeline_run(run_id)
```

**Viewer Export:**
```
export_full_map()  -- returns nested dict for frontend consumption
```

---

### Implementation

#### Files

- `backend/db.py` — All database access: connection management, schema init, CRUD for every entity, export

#### Python Module Interface (`backend/db.py`)

**Connection management:**
- `connect(db_path) -> Connection` — Open (or create) the SQLite file, enable WAL mode and foreign keys
- `init_schema(conn)` — Create all tables if they don't exist
- `close(conn)` — Close the connection

**Pipeline orchestration:**
- `start_pipeline_run(conn, step) -> run_id` — Insert a new run record, return its ID
- `complete_pipeline_run(conn, run_id, status='completed')` — Set `completed_at` and status

**Modules (Part 1):**
- `add_module(conn, name, classification, type, technology, source_origin, deployment_target, run_id) -> module_id`
- `add_module_directories(conn, module_id, paths: list[str])`
- `get_modules(conn, classification=None) -> list[dict]` — Optional filter by classification
- `get_module(conn, module_id) -> dict`
- `get_module_directories(conn, module_id) -> list[str]` — Returns directory paths for a module
- `clear_modules(conn)` — Delete all modules (cascades to directories, components, etc.)

**Components (Part 2):**
- `add_component(conn, module_id, name, purpose, confidence, run_id) -> component_id`
- `add_component_files(conn, component_id, paths: list[str], is_test: list[bool])`
- `get_components(conn, module_id=None) -> list[dict]`
- `get_component(conn, component_id) -> dict`
- `get_component_files(conn, component_id, exclude_test=False) -> list[str]` — Returns file paths; when `exclude_test=True`, filters out rows where `is_test=1`

**Module Edges (Part 1):**
- `add_module_edge(conn, source_id, target_id, edge_type, weight, metadata, run_id)`
- `get_module_edges(conn, source_id=None) -> list[dict]`

**Component Edges (Part 2):**
- `add_component_edge(conn, source_id, target_id, edge_type, weight, metadata, run_id)`
- `get_component_edges(conn, source_id=None) -> list[dict]`

**Decisions (Part 3):**
- `add_decision(conn, category, text, module_id=None, component_id=None, source='pipeline_generated', run_id=None) -> decision_id`
- `get_decisions(conn, module_id=None, component_id=None) -> list[dict]`
- `delete_decisions_by_ids(conn, ids: list[int])` — Delete specific decisions by primary key (used by module elevation to remove promoted component decisions)
- `clear_decisions(conn, source='pipeline_generated')` — Remove only pipeline-generated decisions, preserving human-edited ones

**Research Agent CRUD:**
- `get_decision(conn, decision_id) -> dict | None` — Returns a single decision by ID (used by PATCH/DELETE endpoints)
- `update_decision(conn, decision_id, **fields)` — Update any decision field (category, text, source, etc.)
- `delete_component(conn, component_id)` — Delete component with cascade (component_files, component_edges, decisions)
- `move_component_files(conn, file_paths: list[str], from_component_id: int, to_component_id: int)` — Reassign files between components

**Change Records (Research Agent):**
- `add_change_record(conn, entity_type, entity_id, action, old_value, new_value, origin, baseline_id) -> change_record_id`
- `get_change_records(conn, since_baseline_id=None) -> list[dict]` — Returns change records since the given baseline (or all if None). Queries `WHERE baseline_id = since_baseline_id`. For pre-first-baseline records (should not occur after initial baseline creation in Part 3), query `WHERE baseline_id IS NULL`

**Baselines (Ticket Generation):**
- `create_baseline(conn, run_id) -> baseline_id`
- `get_current_baseline(conn) -> dict | None` — Returns the most recent baseline, or None if no baseline exists

**Tickets (Ticket Generation):**
- `add_ticket(conn, title, description, acceptance_criteria, run_id) -> ticket_id`
- `add_ticket_files(conn, ticket_id, paths: list[str])`
- `add_ticket_decisions(conn, ticket_id, decision_ids: list[int])` — Link ticket to its source decisions
- `link_ticket_change_records(conn, ticket_id, change_record_ids: list[int])`
- `get_tickets(conn, status=None) -> list[dict]`

**Export & Import:**
- `export_full_map(conn) -> dict` — Nested dict with modules → components → decisions + edges, suitable for frontend JSON consumption. Returns:
```json
{
  "modules": [
    {
      "id": 1, "name": "...", "classification": "module", "type": "...",
      "technology": "...", "source_origin": "...", "deployment_target": "...",
      "directories": ["..."],
      "decisions": [{"id": 1, "category": "...", "text": "...", "detail": "...", "source": "..."}],
      "components": [
        {
          "id": 1, "name": "...", "purpose": "...", "confidence": 0.9,
          "files": [{"path": "...", "is_test": false}],
          "decisions": [{"id": 2, "category": "...", "text": "...", "detail": null, "source": "..."}]
        }
      ]
    }
  ],
  "module_edges": [{"source_id": 1, "target_id": 2, "edge_type": "...", "weight": 1.0, "metadata": "..."}],
  "component_edges": [{"source_id": 1, "target_id": 2, "edge_type": "...", "weight": 1.0, "metadata": "..."}]
}
```

- `import_full_map(conn, data) -> dict` — Import a full map from exported JSON (same format as `export_full_map` output). Clears existing modules (CASCADE cleans all dependent data), then rebuilds with ID remapping (exported integer IDs → fresh autoincrement IDs). Returns summary: `{modules, components, decisions, module_edges, component_edges}`. Accepts the raw `export_full_map` format. Preserves decision `source` field (human/pipeline_generated). Silently skips edges with unresolved IDs.

---

### Key Technical Decisions

#### SQLite, not Postgres

**Choice:** One SQLite file per repository, no database server

**Why:** The pipeline runs locally on one repo at a time. SQLite needs no server, no configuration, and the DB file is trivially portable. If multi-user or remote access is ever needed, this can be reconsidered — but for a local analysis tool, SQLite is the right fit.

#### Polymorphic FK on decisions

**Choice:** Decisions have `module_id` OR `component_id`, enforced by a CHECK constraint

**Why:** A decision belongs to exactly one entity. Two nullable FKs with a CHECK constraint is simpler than a separate join table or STI pattern, and SQLite enforces it at the row level.

#### Split edge tables with proper FKs

**Choice:** `module_edges` and `component_edges` are separate tables, each with typed foreign keys to their respective entity tables

**Why:** A single `edges` table with a `level` column cannot enforce referential integrity — `source_id` and `target_id` could point to the wrong entity type. Split tables let SQLite enforce `REFERENCES modules(id)` and `REFERENCES components(id)` with `ON DELETE CASCADE`, making edge cleanup automatic when modules or components are deleted.

#### Join tables for file/directory mappings

**Choice:** `module_directories` and `component_files` are separate join tables, not JSON arrays

**Why:** Indexed lookup — "which module owns this directory?" and "which component owns this file?" are queries that run frequently during the pipeline. JSON arrays would require scanning and parsing; join tables use standard indexes.

#### Fixed category enum

**Choice:** Decision categories are a fixed set of 8 values matching the existing docs

**Why:** Categories come from the map_descriptions spec (api_contracts, patterns, libraries, boundaries, error_handling, data_flow) plus module-level additions (deployment, cross_cutting). Enforcing a fixed set keeps the data consistent and queryable. New categories require a deliberate schema change.

#### Classification column instead of separate tables

**Choice:** Shared libraries and supporting assets are stored in the `modules` table with a `classification` column, not in separate tables

**Why:** The structure is identical — name, type, technology, directories. A classification column avoids schema duplication and lets all pipeline steps query a single table. The `classification` field distinguishes entity types for filtering and display.

#### Change records as audit trail

**Choice:** Change records are permanent — they form the audit trail for ticket generation

**Why:** Every map edit (human or AI-approved) creates a change record. Ticket generation reads change records since the last baseline. Records are never deleted because they represent the history of map evolution. The `baseline_id` field links each change to its generation window.

#### ON DELETE CASCADE for re-runs

**Choice:** Deleting a module cascades to all its children (directories, components, files, module_edges, component_edges, pipeline-generated decisions)

**Why:** When Part 1 is re-run, the simplest cleanup is to delete the old modules and let CASCADE remove dependent data. `module_edges` cascade because both `source_id` and `target_id` reference `modules(id)`. `component_edges` cascade because components cascade from modules, and both edge FKs reference `components(id)`. Human-edited decisions are lost on Part 1 re-run (this is a full rebuild — the frontend warns before proceeding).

---

## Planned Changes

- [x] Implement `backend/db.py` with schema init and connection management
- [x] Implement CRUD functions for modules (with classification), components (with purpose/confidence), edges
- [x] Implement CRUD functions for decisions with polymorphic FK
- [x] Implement change record and baseline functions
- [x] Implement ticket generation functions (with change record traceability)
- [x] Implement `export_full_map()` for frontend consumption
- [x] Add database link to legend_map sub-documents list (already present)
- [x] Implement `import_full_map()` for round-trip JSON import (accepts `export_full_map` format, handles ID remapping)

---

## Log

- 2026-02-16 :: william :: Created doc (Section 1 + Section 2 draft)
- 2026-02-16 :: william :: Added `delete_decisions_by_ids` and `get_component_files` to interface
- 2026-02-16 :: william :: NLC audit: Added classification to modules, purpose/confidence to components, replaced suggestions with change_records/baselines, updated pipeline step interactions, L2 edges now written by Part 1 only, ticket traceability via change records
- 2026-02-16 :: william :: NLC audit fix: Split edges into module_edges + component_edges with proper FKs, added ticket_decisions join table, changed action enum to 'edit', added research agent CRUD functions (update_decision, delete_component, move_component_files), documented baseline_id semantics and re-run strategy, added export_full_map return schema, 12→14 tables
- 2026-02-16 :: william :: Implemented backend/db.py — all 14 tables, full Python API (connection mgmt, pipeline runs, modules, components, edges, decisions, change records, baselines, tickets, export). Smoke tested: CHECK constraints, CASCADE deletes, polymorphic FK, all CRUD functions verified
- 2026-02-18 :: william :: Doc sync: Fixed component_edges UNIQUE constraint to match code (source_id, target_id) instead of (source_id, target_id, edge_type). Updated edge_type comment to 'depends-on'. Added get_module_directories() and get_decision() to Python Module Interface (were implemented but undocumented).
- 2026-02-18 :: william :: Added backend test suite (backend/tests/). test_db.py covers all CRUD operations (53 tests), test_db_export.py covers export_full_map() nested output (14 tests). In-memory SQLite used for fast, isolated tests.
- 2026-02-19 :: william :: Added `import_full_map(conn, data)` to db.py. Accepts `export_full_map()` output format, clears existing data, rebuilds with ID remapping (old→new for modules, components), inserts decisions with proper FK remapping, serializes edge metadata dicts back to JSON strings. Returns summary dict with counts. Added `POST /api/map/import` endpoint to main.py.
- 2026-03-05 :: william :: Added `detail TEXT` nullable column to decisions table. `text` is now a concise one-liner, `detail` holds optional deeper context. Updated `add_decision()`, `export_full_map()`, and `import_full_map()` to handle the new column.
