# Component Discovery

> Part of [Legend Architecture Map](../legend_map/info_legend_map.md)

## Section 1 (HUMAN EDITS ONLY)

### Purpose

Given a module's source directories, discover C4 Level 3 components by parsing code dependencies and clustering files that work closely together. A component is a cohesive group of files encapsulated behind a well-defined interface — not a deployment unit (that's a module), but a responsibility boundary within one.

---

### How it Works

#### Workflow

The pipeline has two phases: **Nodes** (discover components) and **Edges** (compute and label dependencies between them).

**Phase 1 — Nodes**

1. **Build SCIP index** — Run a language-specific SCIP indexer against the module's source directories to produce a `.scip` protobuf file containing every symbol definition, reference, and relationship in the code. See [SCIP Indexing](./info_component_discovery_scip_indexing.md).

2. **Parse the SCIP index** — Single-pass with deferred resolution: first iterate all documents collecting definitions (symbol-to-file map) and SymbolInformation.kind values; then collect all non-definition, non-local references into a deferred list. After all documents are processed, resolve each reference to its defining file. Extract three edge types:
   - **Call edges** (weight 1.0) — cross-file method/function references
   - **Import edges** (weight 0.3) — cross-file import statements
   - **Inheritance edges** (weight 0.5) — `Relationship.is_implementation` links

3. **Exclude test files** — Separate source files from test files using language-agnostic patterns (`test/`, `tests/`, `__tests__/`, `spec/`, `test_*.ext`, `conftest.py`, `fixtures/`, `mocks/`). Tests are not C4 components — they don't expose interfaces. Only source-to-source edges enter the clustering graph.

4. **Build weighted graph** — Nodes are files. Edges carry the weighted sum of call + import + inheritance counts. Two graph-level adjustments:
   - **Directory affinity** — Files sharing a directory get bonus weight (scaled inversely with directory size: `BASE / log2(n)` for dirs with >4 files, to prevent large flat dirs from creating mega-clusters)
   - **Hub dampening** — High-degree nodes (90th percentile by degree) have their edge weights divided by `sqrt(degree/threshold)`, reducing the gravitational pull of utility files that connect to everything

5. **Leiden clustering** — Run the Leiden community detection algorithm to partition the graph. Resolution auto-scales with graph size: `base * (1 + 0.5 * log2(nodes/50))`, capped at 2x base. Uses `RBConfigurationVertexPartition`.

6. **Post-cluster redistribution** — Move files where >60% of call weight goes to a cluster other than the one they were assigned to. This fixes utility/shared files that hub effects pulled into the wrong cluster.

7. **Name components** — Name each cluster by its dominant leaf directory. Deduplicate collisions by prepending the parent directory, then adding numeric suffixes if still colliding.

8. **Assign tests** — Map each test file to the source component it references most, using a fallback chain: (1) call-edge weight to source components, (2) path similarity (`test_selector.py` matches the component containing `selector/`), (3) "unassigned".

9. **LLM refinement** — For each Leiden cluster, send file metadata and internal dependency edges to an LLM. The LLM assesses cohesion, splits clusters that combine unrelated responsibilities, names components semantically, and flags misplaced files. See LLM Refinement section below.

10. **Reconcile misplaced files** — Process the LLM's misplaced-file suggestions across all clusters. Move files to their suggested component if it exists. Resolve conflicts (two clusters claim same file) by assigning to the higher-confidence cluster. Files that can't be reconciled stay in their original cluster.

**Phase 2 — Edges**

11. **Aggregate** — For each file-level SCIP edge (call/import/inheritance), look up which components the source and target files belong to. If different components, that's a component edge. Each edge type (call, import, inheritance) produces a **separate edge** per component pair. Weights are **normalized by component size**: `raw_count / sqrt(source_file_count × target_file_count)`. This makes weights represent coupling density rather than raw reference counts, so small tightly-coupled components get appropriately high weights.

12. **LLM label** — Single batched LLM call labels all edges with 3-8 word phrases describing the dependency. Works in-memory before DB write. Edges truncated to top 30 by weight if too many. On failure: edges get `label=""`, pipeline continues.

13. **Write to DB** — Components + labeled edges written together. Each edge stored with its original `edge_type` ("call", "import", or "inheritance") and metadata containing label.

#### LLM Refinement (Step 9)

After algorithmic clustering, an LLM reviews each Leiden cluster and refines the component assignments. For each cluster, the LLM receives file metadata (docstrings, exports, symbol kinds), top internal call edges, and directory breakdown. It then:
- Assesses cluster cohesion (high/medium/low)
- Decides whether to split the cluster into 2-4 sub-components
- Names components with domain-specific snake_case names (e.g. `http_request_handler`, not `utils_1`)
- Identifies misplaced files that belong in a different component
- Assigns a confidence score (0.0-1.0)

This is a core pipeline step, not optional. If the LLM call fails for a cluster, the pipeline degrades gracefully: the Leiden cluster becomes a single component with directory-based naming and confidence=0.3.

#### Rules & Edge Cases

- Local symbols (`local N`) are always skipped — they are file-scoped by SCIP design and have no cross-file meaning
- `scip-python` does not emit `SymbolRole.Import`, so import edges = 0 for Python codebases; inheritance edges still contribute
- Method/function detection uses a dual strategy: check `SymbolInformation.kind` enum first, fall back to parsing the symbol string suffix (`().`) if no kind entry exists
- Isolated files (no cross-file references) each become their own single-file component
- The seed is fixed (42) for reproducibility across runs

---

## Sub-documents

- [SCIP Indexing](./info_component_discovery_scip_indexing.md) — Building `.scip` files from source code

---

## Section 2 (HUMAN + AI)

### Architecture

#### Data Model

**Input:**
- `.scip` protobuf file (whole-codebase, from SCIP Indexing step)
- Module definitions from the database (queried via `get_modules()` — includes name, classification, directories)
- Source directory path (for docstring/LOC extraction)

**Intermediate structures:**

```
ModuleScopedSCIP:
  edges: {(file_a, file_b): call_count}
  files: set of file paths within module
  definitions: count
  references: count

FileMetadata (per file):
  path, directory, docstring, imports, exports,
  symbol_kinds, leiden_cluster, lines_of_code

ClusterAnalysis (LLM output per Leiden cluster):
  cluster_id, primary_responsibility, cohesion,
  should_split, components[], misplaced_files[], confidence
```

**Output (per module):**

```json
{
  "module_name": "backend_api",
  "components": [
    {
      "name": "http_request_handler",
      "purpose": "Handles incoming HTTP requests, routing, and response serialization",
      "confidence": 0.9,
      "files": [
        {"path": "backend/routes/users.py", "is_test": false},
        {"path": "tests/test_routes.py", "is_test": true}
      ]
    }
  ],
  "edges": {
    "L3": [
      {
        "source": "http_request_handler",
        "target": "data_access_layer",
        "edge_type": "call",
        "weight": 3.46,
        "metadata": {
          "label": "queries user records"
        }
      },
      {
        "source": "http_request_handler",
        "target": "data_access_layer",
        "edge_type": "import",
        "weight": 1.15,
        "metadata": {
          "label": "imports data models"
        }
      }
    ]
  },
  "stats": {
    "total_files": 45,
    "source_files": 38,
    "test_files": 7,
    "leiden_clusters": 6,
    "final_components": 5,
    "resolution": 1.73,
    "llm_calls": 6
  }
}
```

#### System Flow

```
For each module from Part 1:

  Phase 1 — Nodes
  ───────────────
  [Filter SCIP] Parse whole-codebase .scip, keep files within module dirs
       |
  [Split source/test] Separate using language-agnostic test patterns
       |
  [Build graph] Nodes=files, edges=call counts, +directory affinity, +hub dampening
       |
  [Leiden v3] Auto-resolution, RBConfigurationVertexPartition, seed=42
       |
  [Redistribute] Move files where >60% call weight goes to another cluster
       |
  [Name clusters] Dominant leaf directory, deduplicate collisions
       |
  [Extract metadata] Per-file: docstrings, exports, symbol kinds, LOC from SCIP+source
       |
  [LLM per cluster] Cohesion assessment, split decisions, semantic naming, misplaced files
       |
  [Reconcile] Process misplaced file suggestions, resolve conflicts
       |
  [Assign tests] Fallback: call-edge weight -> path similarity -> "unassigned"

  Phase 2 — Edges
  ───────────────
  [Aggregate] Combine file edges → one edge per component pair (weighted sum)
       |
  [LLM label] Single batched call → 3-8 word label per edge (in-memory)
       |
  [Write to DB] Components + labeled edges together via db.py
```

#### Key Components

| File | Responsibility | Ported from |
|------|---------------|-------------|
| `pipeline.py` | Orchestrator — sequences all steps per module | `Leiden_hybrid/pipeline.py` + `LLM_based/main.py` |
| `scip_filter.py` | Parse SCIP protobuf, filter to module scope by dir prefix | New (uses `Leiden_hybrid/scip_parser.py`) |
| `graph_builder.py` | Weighted networkx graph, directory affinity, hub dampening | `Leiden_hybrid/graph_builder.py` |
| `leiden_cluster.py` | Leiden wrapper + auto-resolution + post-redistribution | `Leiden_hybrid/leiden_cluster.py` + `pipeline.py` |
| `component_namer.py` | Name clusters by dominant directory, deduplicate | `Leiden_hybrid/component_namer.py` |
| `metadata_extractor.py` | Per-file SCIP + source metadata | `LLM_based/metadata_extractor.py` |
| `cluster_analyzer.py` | LLM analysis per cluster, split/merge/fallback | `LLM_based/cluster_analyzer.py` |
| `prompts.py` | System prompt + cluster_analysis_prompt | `LLM_based/prompts.py` (no module_discovery_prompt) |
| `llm_client.py` | LLM API wrapper (litellm), retry, JSON parsing | `LLM_based/llm_client.py` |
| `test_filter.py` | Test file detection, language-agnostic patterns | `Leiden_hybrid/pipeline.py` (extracted) |
| `edge_aggregator.py` | Combines all file-level SCIP edges into ONE edge per component pair. Weight = total cross-component file edge count. No filters or caps. | New |
| `edge_labeler.py` | Pure in-memory LLM labeling: takes edge+component dicts, returns edges with label added. Single batched call, graceful failure | New |

### Implementation

#### Entry Points

```python
# Single module
discover_components(conn, module: dict, scip_path, source_dir) -> None

# All modules from Part 1 — reads modules from DB, writes results directly to DB
discover_all_components(conn, scip_path, source_dir) -> None
```

`discover_all_components` reads modules from the database via `get_modules(conn)` and writes components, component_files, and component_edges directly to DB. No intermediate JSON return.

#### Dependencies

`protobuf`, `networkx`, `igraph`, `leidenalg`, `litellm`, `python-dotenv`

#### Module Filter

Skip modules where `classification = 'supporting-asset'` or `source_origin != 'in-repo'`. These have no indexable source code.

#### Database Integration

The pipeline writes directly to the database (no intermediate JSON):
- Components to `components` table via `add_component(conn, module_id, name, purpose, confidence, run_id)`
- File mappings to `component_files` table via `add_component_files(conn, component_id, paths, is_test)`
- L3 edges to `component_edges` table via `add_component_edge(conn, source_id, target_id, edge_type, weight, metadata, run_id)`

Each edge type (call, import, inheritance) produces a separate edge per component pair. Weights are normalized by component size: `raw_count / sqrt(source_files × target_files)`. The `metadata` column stores a JSON object: `{"label": "validates auth tokens"}`. The `component_edges` table has `UNIQUE(source_id, target_id, edge_type)`.

**Edge name → ID resolution:** After calling `add_component()` for each component in a module, build `name_to_id = {component_name: component_id}`. Then for each L3 edge, resolve `source` and `target` names to integer IDs before calling `add_component_edge()`.

Note: L2 edges are written by Part 1, not Part 2. Part 2 only writes L3 (component-to-component) edges to `component_edges`.

### Key Technical Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Clustering method | v3 file-level Leiden only | LLM refinement handles cases v3 gets wrong (utility fragmentation). Simpler than hybrid. Finer-grained clusters are easier for LLM to merge than coarse clusters are to split. |
| LLM granularity | One call per Leiden cluster | Per-file too expensive. Batch too large for context. Per-cluster is the natural unit (5-30 files, ~5K tokens). |
| SCIP scope | Parse whole codebase once, filter per module | Running indexers per module is fragile (needs separate build configs). Parsing is fast. Dir-prefix filtering is O(n). Cross-module edges get correctly excluded from intra-module graph. |
| SCIP pre-build in Part 1 | Part 1 runs SCIP indexer in parallel with OpenCode; Part 2 reuses the `.scip` files if present | SCIP indexing is slow and has no dependency on Part 1's JSON output. Running it in parallel hides the cost. Part 2 checks `output/*.scip` first and falls back to running the indexer only if needed. |
| Multi-language SCIP merging | `parse_scip_for_module` accepts a list of `.scip` paths; results are merged per module | The indexer produces separate files per language (e.g. `python.scip`, `typescript.scip`). Using only the first file silently drops all modules written in other languages. Merging across all files ensures every module directory is matched regardless of language. |
| LLM failure mode | Degrade to Leiden-only naming, confidence=0.3 | Pipeline must complete without LLM. Low confidence signals Part 3 to treat descriptions as provisional. |
| Separate typed edges with normalized weights | Keep call/import/inheritance as separate edge types per component pair; weight = raw_count / sqrt(source_files × target_files) | Separate types enable per-type filtering in the UI (e.g. show only call edges). Geometric mean normalization makes weight represent coupling density rather than raw counts — a weight of 5.0 between two 2-file components is genuinely strong, while 0.2 between two 50-file components is weak. |
| Prompt truncation | Cap at 30 files per cluster if >8K tokens | Directory breakdown and top edges still provide enough signal. Better to truncate than to skip the LLM call entirely. |

---

## Planned Changes

- [x] Port Leiden v3 pipeline from research code
- [x] Port LLM cluster analysis from research code
- [x] Implement SCIP module-scope filtering
- [x] Implement L3 edge aggregation
- [x] Integrate with database
- [x] Fix: edge aggregation uses SCIP-derived file paths, not LLM-returned file lists
- [x] Add tests

### Separate Typed Edges + Normalized Weights
- [x] Refactor edge_aggregator to produce separate call/import/inheritance edges instead of "depends-on"
- [x] Add geometric mean normalization: weight = raw_count / sqrt(src_files × tgt_files)
- [x] Update edge_labeler to include edge_type in LLM prompt and label lookup
- [x] DB migration: change UNIQUE(source_id, target_id) to UNIQUE(source_id, target_id, edge_type)
- [x] Update backend tests for new edge behavior

---

## Log

- 2026-02-16 :: william :: Created doc (Section 1 draft, derived from Leiden_hybrid research)
- 2026-02-16 :: william :: Filled in Section 2 (architecture, implementation, key technical decisions). Decided: v3 Leiden + LLM refinement, no Leiden_2. Updated Section 1 to remove Two Methods and make LLM refinement core.
- 2026-02-16 :: william :: NLC audit: Removed from_cluster from output (debug-only), removed L2 edge writing (Part 1 writes L2), clarified input comes from DB, updated DB integration section
- 2026-02-16 :: william :: NLC audit fix: Updated function sig to read/write DB directly, added module filter (skip supporting-assets + non-in-repo), added edge name→ID resolution, updated edge functions to add_component_edge
- 2026-02-16 :: william :: Implemented backend/component_discovery/ package — 11 modules ported from research code (Leiden_hybrid + LLM_based). scip_filter.py (module-scope SCIP parsing), test_filter.py, graph_builder.py (3 edge types), leiden_cluster.py, component_namer.py, metadata_extractor.py, prompts.py, llm_client.py, cluster_analyzer.py, edge_aggregator.py (new), pipeline.py (full orchestrator with DB integration). Added get_module_directories() to db.py. Smoke tested: Leiden clustering, edge aggregation, DB write/export all verified
- 2026-02-16 :: william :: SCIP pre-build optimisation: Part 1 now runs the SCIP indexer in parallel with OpenCode. part2_stream() checks for an existing .scip file and skips the indexer if found. Added SCIP pre-build decision to Key Technical Decisions.
- 2026-02-16 :: william :: Bug fix: component edges were not being generated. Root cause: cluster_analyzer.py used LLM-returned file paths to build file→component mapping for edge aggregation. LLM can return wrong/shortened paths that don't match SCIP keys, causing all file_to_comp lookups to return None. Fix: non-split case now always uses original Leiden file_paths; split case validates LLM paths against original set and assigns unmatched files to first sub-component. LLM controls naming and split decisions only — file path assignment comes from SCIP.
- 2026-02-16 :: william :: Bug fix: UNIQUE constraint failure when LLM assigns identical names to different Leiden clusters (e.g. two clusters both named 'toast_notification_ui'). Fix: added _deduplicate_components() step in _write_to_db() that merges components with the same name — combining their files (deduped) and keeping the higher confidence score.
- 2026-02-16 :: AI :: Added edge_labeler.py: after the "edges" step writes SCIP-derived edges to DB, it makes a single batched LLM call per module to label each (source, target) pair with a concise phrase. Label stored in component_edges.metadata as {"label": "..."}. LLMClient reused from component_discovery package.
- 2026-02-16 :: AI :: Bug fix: pipeline only used one SCIP file (most recently modified), so Python/TypeScript/JavaScript modules were silently skipped when a different language's SCIP file was selected first. Root cause: multiple language-specific .scip files (python.scip, typescript.scip, javascript.scip) exist in output/ but only the first was passed to discover_all_components. Fix: parse_scip_for_module now accepts str | list[str]; when given a list it parses each file and merges the results. part2_stream now passes all .scip files as a list, main.py and pipeline.py type hints updated.
- 2026-02-17 :: william :: Migrated LLM client from anthropic SDK to litellm for provider-agnostic model support. LLMClient now uses litellm.completion() with api_key passthrough. DEFAULT_MODEL updated to include provider prefix (anthropic/claude-sonnet-4-20250514). Part 2 and edges steps now pass the user-selected model from the frontend.
- 2026-02-17 :: william :: Restructured component pipeline into Nodes + Edges phases. edge_aggregator.py combines all file-level SCIP edges into ONE edge per component pair (weight = total count, no filters or caps). Note: call/import/inheritance weights (1.0/0.3/0.5) are applied in graph_builder.py at the file-level graph stage, not during edge aggregation. edge_labeler.py refactored to pure in-memory function (no DB reads/writes). pipeline.py split into Phase 1 (Nodes, steps 1-10) and Phase 2 (Edges: aggregate → label → write). DB schema: component_edges UNIQUE changed from (source_id, target_id, edge_type) to (source_id, target_id). main.py edges stream handler updated. AnimatedEdge.tsx: added "depends-on" color entry.
- 2026-02-18 :: william :: Added backend test suite (backend/tests/). Tests cover: llm_client._parse_json_response() (both copies, 28 tests), edge_aggregator.aggregate_component_edges() (10 tests), test_filter.is_test_file()/split_source_and_test() (25 tests), scip_filter pure helpers (21 tests). 189 total tests across 8 files, all passing.
- 2026-02-20 :: william :: Refactored L3 edge system: edge_aggregator now produces separate call/import/inheritance edges (instead of merging into "depends-on") with geometric mean normalization (weight = raw_count / sqrt(src_files × tgt_files)). DB schema: component_edges UNIQUE changed back to include edge_type. edge_labeler updated for typed edges with top-30 truncation. Backend tests updated (14 tests, all passing, 188 total suite).
