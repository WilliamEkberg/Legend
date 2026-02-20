> Part of [OpenCode CLI Web Wrapper](./info_opencode_runner.md)

# L2 Clustering — C4 Level 2 Classification

## Section 1 (HUMAN EDITS ONLY)

### Purpose

Defines the C4 Level 2 (Module) classification logic — the prompts, workflow, and output schema that the OpenCode CLI agent follows to classify a codebase into modules, shared libraries, and supporting assets. The process is split into two sequential steps to keep each LLM task focused and reliable.

---

### How it Works

The process runs as two sequential OpenCode invocations:

**Step 1 — Identify Modules:**
The LLM is given a focused set of context files to read (workspace configs, manifests, Dockerfiles, docker-compose, deployment markers). It identifies what modules exist, giving each a string ID, name, classification, and directories. No dependency graph is needed. Shared libraries and other independent units (caches, queues, databases) are also classified here at L2 abstraction. Output: `c4_level2_modules_<timestamp>.json`.

**Step 2 — Identify Edges:**
The LLM is told to first read the modules JSON from Step 1. With that module list in hand, it then reads manifest files to identify which modules depend on each other. Output: `c4_level2_edges_<timestamp>.json`.

The backend ingests Step 1 output first (modules into DB), builds an ID map (string → integer), then ingests Step 2 output (edges into DB using the ID map).

#### What is a Module at L2

At L2, a module is any **independently meaningful unit** at a high level of abstraction. This includes:
- Separately deployable apps (web apps, APIs, workers, serverless functions)
- Data stores (databases, caches, queues, file systems) — even managed ones
- Shared libraries consumed by other units
- Supporting infrastructure (CI/CD, tooling, docs) — classified as `supporting-asset`

The distinction from L3 (components): L2 modules are the big boxes in the architecture diagram. L3 components are the smaller groupings of code files inside a module.

#### Rules & Edge Cases

- Step 1 does NOT build a dependency graph — that's Step 2's job
- Step 2 MUST read the modules JSON before outputting edges (the LLM needs the module IDs)
- Shared libraries appear as modules with `classification: "shared-library"` — they get `consumedBy` populated in Step 2, not Step 1
- Supporting assets (CI, docs, scripts) appear with `classification: "supporting-asset"` — they have no edges

---

## Sub-documents

*(none)*

---

## Section 2 (HUMAN + AI)

### Architecture

#### Prompt Structure

Two separate OpenCode invocations, each with its own prompt:

```
Step 1 — Modules
┌──────────────────────────────┐
│ IDENTITY_PROMPT              │  "You are an autonomous coding agent..."
├──────────────────────────────┤
│ MODULE_CONTEXT               │  What L2 modules are + classification rules
├──────────────────────────────┤
│ MODULE_TASK (variables)      │  What files to read, output file name
└──────────────────────────────┘

Step 2 — Edges
┌──────────────────────────────┐
│ IDENTITY_PROMPT              │  "You are an autonomous coding agent..."
├──────────────────────────────┤
│ EDGES_CONTEXT                │  What edges mean + edge type rules
├──────────────────────────────┤
│ EDGES_TASK (variables)       │  Read modules JSON first, output file name
└──────────────────────────────┘
```

#### Output Schemas

**Modules JSON** (`c4_level2_modules.json`):

```jsonc
{
  "metadata": {
    "generatedAt": "2026-02-17T..."
  },
  "modules": [
    {
      "id": "backend-api",              // short kebab-case string, unique
      "name": "Backend API",            // human-readable name
      "classification": "module",       // "module" | "shared-library" | "supporting-asset"
      "type": "api-service",            // see type list below
      "technology": "fastapi",          // primary tech/framework
      "directories": ["backend/"],      // relative paths claimed by this module
      "sourceOrigin": "in-repo",        // "in-repo" | "external"
      "deploymentTarget": "docker",     // where it runs
      // only for shared-library:
      "packageName": "@org/ui-lib",
      // only for supporting-asset:
      "category": "ci-cd"
    }
  ]
}
```

No `relationships` or `consumedBy` field in the modules JSON — those come from Step 2.

**Edges JSON** (`c4_level2_edges.json`):

```jsonc
{
  "edges": [
    {
      "sourceId": "backend-api",        // matches id from modules JSON
      "targetId": "postgres-db",        // matches id from modules JSON
      "type": "uses_data_store",        // "depends_on" | "communicates_via" | "uses_data_store"
      "protocol": "rest",               // only for communicates_via
      "description": "Stores user records and sessions"
    }
  ],
  // for shared-library modules only — who consumes them
  "consumedBy": [
    {
      "libraryId": "ui-components",
      "consumerIds": ["web-app", "admin-app"]
    }
  ]
}
```

#### Key Components

| File | Responsibility |
|------|----------------|
| `backend/prompts.py` | `IDENTITY_PROMPT`, `MODULE_CONTEXT`, `EDGES_CONTEXT`, `modules_system_prompt()`, `edges_system_prompt()`, `modules_variables_prompt()`, `edges_variables_prompt()` |
| `backend/main.py` | `ingest_l2_modules()` (Step 1 DB write), `ingest_l2_edges()` (Step 2 DB write), `part1_stream()` (runs Step 1 then Step 2 sequentially) |

---

### Implementation

#### Prompt Design — Step 1 (Modules)

**System prompt tells the LLM to:**
1. Read the context files listed in the task prompt (workspace configs, manifests, Dockerfiles, docker-compose, deployment markers — do NOT read source code files)
2. Identify every module at L2 level: separately deployable apps, data stores, shared libraries, supporting assets
3. Assign each a short kebab-case string ID and descriptive name
4. Claim directories for each module
5. Output the modules JSON — no relationships, no dependency graph

**Context files the LLM should read** (provided in the variables prompt):
- Root: `package.json`, `pnpm-workspace.yaml`, `yarn.lock`, `go.work`, `Cargo.toml`, `pyproject.toml`, `docker-compose*.yml`
- Glob: all `*/package.json`, all `Dockerfile*`, all deployment markers (`vercel.json`, `serverless.yml`, `wrangler.toml`, etc.)

#### Prompt Design — Step 2 (Edges)

**System prompt tells the LLM to:**
1. Read the modules JSON from the output directory FIRST (before doing anything else)
2. Read manifest files to find workspace dependencies and SDK/client library imports
3. Determine which modules depend on which other modules
4. Output the edges JSON with `sourceId`, `targetId`, `type`, optional `protocol`, `description`
5. Also populate `consumedBy` for shared libraries

**Edge type rules:**
- `depends_on` — one module uses another as a library/package (manifest dependency)
- `communicates_via` — runtime API call between running services (needs `protocol`)
- `uses_data_store` — module connects to a DB, cache, queue, or storage module

#### DB Ingestion

**`ingest_l2_modules(conn, json_path, run_id) -> dict`**

Reads the modules JSON and inserts modules. Returns `id_map: {string_id: db_integer_id}`.

```python
id_map = {}
for entry in json_data["modules"]:
    module_id = db.add_module(conn, name=entry["name"],
                              classification=entry.get("classification", "module"),
                              type=entry.get("type"),
                              technology=entry.get("technology"),
                              source_origin=entry.get("sourceOrigin"),
                              deployment_target=entry.get("deploymentTarget"),
                              run_id=run_id)
    directories = entry.get("directories", [])
    if directories:
        db.add_module_directories(conn, module_id, directories)
    id_map[entry["id"]] = module_id
return id_map
```

**`ingest_l2_edges(conn, json_path, id_map, run_id) -> None`**

Reads the edges JSON and inserts module edges. Uses `id_map` to resolve string IDs → DB integer IDs.

```python
# Process direct edges
for edge in json_data.get("edges", []):
    source_id = id_map.get(edge["sourceId"])
    target_id = id_map.get(edge["targetId"])
    if not source_id or not target_id:
        continue
    edge_type = edge["type"]
    description = edge.get("description", "")
    protocol = edge.get("protocol", "")
    if edge_type == "communicates_via":
        label = f"{protocol}: {target_name}" if protocol else f"calls {target_name}"
        metadata = json.dumps({"label": label, "protocol": protocol, "description": description})
    elif edge_type == "uses_data_store":
        metadata = json.dumps({"label": f"stores data in {target_name}", "description": description})
    else:  # depends_on
        metadata = json.dumps({"label": f"uses {target_name}", "description": description})
    db.add_module_edge(conn, source_id, target_id, edge_type, 1.0, metadata, run_id)

# Process consumedBy (shared libraries)
for cb in json_data.get("consumedBy", []):
    library_id = id_map.get(cb["libraryId"])
    if not library_id:
        continue
    for consumer_str_id in cb.get("consumerIds", []):
        consumer_id = id_map.get(consumer_str_id)
        if not consumer_id:
            continue
        key = (consumer_id, library_id)
        # Only add if not already added via edges array
        db.add_module_edge(conn, consumer_id, library_id, "depends_on", 1.0,
                           json.dumps({"label": f"uses {library_name}"}), run_id)
```

#### Streaming Orchestration (`part1_stream`)

Step 1 and Step 2 run sequentially inside the same `part1_stream` generator:

```
1. Run opencode (modules prompt)
   → find c4_level2_modules_*.json
   → call ingest_l2_modules() → id_map

2. Run opencode (edges prompt)
   → find c4_level2_edges_*.json
   → call ingest_l2_edges(id_map)
```

Both opencode calls use the same `env` (API key). The SCIP indexer runs in parallel with Step 1 (same as before).

#### Backwards Compatibility

The old format (single `c4_level2_*.json` with `relationships`) is still handled by `ingest_l2_output()` for the non-streaming `/api/run` endpoint. New 2-step format is only used in the streaming `part1_stream`.

---

### Key Technical Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Two-step instead of one | Modules JSON then Edges JSON | Asking one LLM call to simultaneously identify modules AND build the dependency graph was too complex. Two focused tasks produce more reliable results. |
| LLM re-reads modules JSON in Step 2 | Explicit instruction to read JSON first | Ensures Step 2 uses the exact same IDs as Step 1. LLM doesn't rely on memory from Step 1. |
| No dependency graph in Step 1 | Modules only, no relationships | Step 1 is about identifying boundaries, not connections. Simpler prompt = more reliable output. |
| Shared libraries as modules | `classification: "shared-library"` in modules array | Shared libs are valid L2 entities. The edges JSON carries the `consumedBy` information. |
| `consumedBy` in edges JSON | Separate from direct `edges` array | Natural fit: who-consumes-what is an edge concept, not a module property. |
| In-memory `id_map` across two steps | Kept in `part1_stream` generator scope | Modules must be inserted before edges can reference them. id_map is small (one entry per module) and doesn't need persistence. |
| Context file selection | Variables prompt lists which files to read | LLM doesn't need to scan the entire codebase to identify L2 modules — a targeted set of deployment/manifest files is sufficient. |

---

## Planned Changes

- [x] Define 2-step workflow and output schemas
- [x] Write MODULE_CONTEXT and EDGES_CONTEXT prompts
- [x] Implement `ingest_l2_modules()` and `ingest_l2_edges()`
- [x] Update `part1_stream()` to run Step 1 then Step 2

---

## Log

- 2026-02-10 :: william :: Created workflow and prompts.py as part of initial implementation
- 2026-02-16 :: william :: Extracted L2 clustering into its own sub-document from info_opencode_runner.md
- 2026-02-16 :: william :: Consolidated all prompts into single prompts.py file. Deleted W.md, workflow.md, C4 Levels definition.md. Removed unused JSON_schema_format_prompt. Removed repoCommit/repositoryUrl from output schema.
- 2026-02-16 :: william :: NLC audit: Unified output schema — single modules[] array with classification field (replaces separate sharedLibraries/supportingAssets arrays). Added DB ingestion section.
- 2026-02-16 :: william :: NLC audit fix: Expanded DB ingestion with ingest_l2_output function, string-to-integer ID mapping, and relationship→edge mapping using add_module_edge
- 2026-02-16 :: william :: Streamlined L2_WORKFLOW from 12 steps to 7: merged orient+workspaces (Step 1), merged all deployment artifact detection into one pass (Step 3), folded catch-remaining into classify (Step 4), moved API definitions after classification (Step 5), compressed relationships (Step 6)
- 2026-02-17 :: william :: Redesigned to 2-step approach: Step 1 identifies modules (no edges/dependency graph), Step 2 reads modules JSON then identifies edges. Simpler focused prompts. Separate JSON files. Updated DB ingestion to ingest_l2_modules() + ingest_l2_edges(). Updated streaming to run both steps sequentially.
- 2026-02-17 :: AI :: Bug fix: Step 2 (edges) stdout/stderr were read sequentially which can deadlock when the stderr pipe buffer fills while waiting for stdout to close. Fixed by draining both pipes concurrently via asyncio tasks + queue (same pattern as Step 1). Added edges_rc error reporting and richer "[DB] No edges JSON found" diagnostic message.
- 2026-02-17 :: AI :: Bug fix: Removed timestamps from output filenames. LLMs frequently ignore exact timestamp format instructions, causing the glob to miss the file. Switched to fixed filenames `c4_level2_modules.json` and `c4_level2_edges.json`. Added JSON file listing diagnostic after each step so you can see what was actually written. Also added explicit instruction in edges prompt to write the file even when no edges exist.
