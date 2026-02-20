# Re-validation Pipeline & Map Versioning

## Section 1 (HUMAN EDITS ONLY)

### Purpose

After the full pipeline (Parts 1-3) has been run once, the codebase may evolve. The re-validation pipeline re-checks whether existing architectural decisions still match the actual source code. It only updates decisions that have concretely changed — no reframing of correct decisions. Changed modules and components are highlighted purple in the map UI. The system also provides full map versioning: snapshots of all decisions at key moments, browsable and comparable in the frontend.

---

### How it Works

#### Re-validation Workflow

1. **Snapshot before** — Take a full snapshot of all current decisions (creates a map version).

2. **New file classification** — Detect and assign files added since last pipeline run:
   - Walk each module's directories on disk and collect all source files (skip binary files, apply test_filter)
   - Query `component_files` for all tracked paths
   - Diff: files on disk but not in DB = new unassigned files
   - Group new files by their parent module (via `module_directories` prefix matching)
   - Files not matching any module directory = **orphans** (logged, skipped)
   - For each module with new files, send to LLM: new file contents + list of existing components (name, purpose, file paths). LLM responds per file: assign to existing component or propose a new component (name + purpose).
   - Write results: add files to `component_files`, create new components if needed
   - After this phase, Phase A naturally picks up the new files since they're now tracked

3. **Component re-validation (bottom-up)** — For each component:
   - Read the component's source files from disk (same as Part 3)
   - Read the component's existing decisions from the database
   - Send code + decisions to the LLM, asking: "Do these decisions still match this code?"
   - LLM classifies each decision as:
     - **Confirmed** — Still accurate. No change.
     - **Updated** — Code changed, decision text needs updating. LLM provides new text.
     - **Outdated** — Decision no longer applies (code pattern was removed).
     - **New** — Code has a pattern not captured by any existing decision.
   - For human-edited decisions, the LLM does NOT modify them but classifies:
     - **Implemented** — Code now matches what the human described (code caught up).
     - **Diverged** — Code moved away from the human's intent.
     - **Unchanged** — Decision still pending, code hasn't changed.

4. **Module re-validation (top-down)** — For each module:
   - Collect the component-level key decisions (now up-to-date after step 2)
   - Read the module's existing decisions (cross_cutting + deployment)
   - Send component decisions + module decisions to the LLM
   - Same classification: confirmed / updated / outdated / new
   - **Important**: Module validation takes component DECISIONS as input, not code

5. **Snapshot after** — Take another full snapshot, creating a before/after version pair.

6. **Display** — Changed nodes get purple highlighting. Each decision shows its validation status in the detail panel.

#### Version Snapshots

Snapshots capture the full state of all decisions at a point in time. They are created:
- Automatically after Part 3 completes
- Automatically before and after each re-validation run
- Manually by the user via "Save Snapshot" button

Users can browse any version to see what the map looked like at that point, and compare any two versions to see what changed (added, removed, modified decisions).

#### Key Rules

- Re-validation changes are **map corrections only** — they do NOT create change records and do NOT feed into ticket generation
- Human-edited decisions are annotated but NEVER modified or deleted by re-validation
- Decisions should NOT be updated if they haven't changed — no reframing of correct wording
- The LLM must be conservative: only flag a decision as changed if there's a concrete factual difference

---

## Section 2 (HUMAN + AI)

### Architecture

#### Data Model

**New tables:**

```
map_versions
  id, version_number (unique, auto-incremented), trigger ('part3'|'revalidation'|'manual'),
  pipeline_run_id, summary (JSON), created_at

version_decisions
  id, version_id (FK), decision_id (nullable), module_id, component_id,
  module_name (denormalized), component_name (denormalized),
  category, text, source

validation_runs
  id, pipeline_run_id, before_version_id (FK), after_version_id (FK),
  model, status, summary (JSON), created_at

decision_validations
  id, validation_run_id (FK), decision_id (nullable),
  source ('pipeline_generated'|'human'),
  status ('confirmed'|'updated'|'outdated'|'new'|'implemented'|'diverged'|'unchanged'),
  old_text, new_text, reason, category, module_name, component_name, created_at
```

**Denormalization:** `version_decisions` stores `module_name` and `component_name` so snapshots survive module/component deletion from Part 1 re-runs.

**Ticket isolation:** Re-validation writes to `decision_validations` exclusively, never to `change_records`. The ticket pipeline reads only `change_records`, guaranteeing zero contamination.

#### System Flow

```
Re-validation Pipeline:
    │
    ├─ create_map_version(trigger='revalidation')     → before_version_id
    ├─ start_validation_run(before_version_id, model)  → val_run_id
    │
    ├─ Phase 0: New File Classification (no LLM for detection, LLM for classification)
    │   ├─ Walk all module directories on disk → all_disk_files
    │   ├─ Query component_files table → all_tracked_files
    │   ├─ Diff: new_files = all_disk_files - all_tracked_files
    │   ├─ Group new_files by module (directory prefix match)
    │   ├─ Log orphan files (no module match)
    │   └─ For each module with new files (parallel, MAX_WORKERS=5):
    │       ├─ Read new file contents from disk
    │       ├─ Read existing components (name, purpose, file paths)
    │       ├─ LLM: "assign each file to existing component or propose new component"
    │       └─ Write: INSERT component_files + optionally INSERT components
    │
    ├─ Phase A: Component Re-validation
    │   └─ For each eligible component (parallel, MAX_WORKERS=5):
    │       ├─ Read source files (reuse _read_component_source)
    │       ├─ Read existing decisions (split by source)
    │       ├─ Pipeline decisions → LLM comparison → confirmed/updated/outdated/new
    │       ├─ Human decisions → LLM implementation check → implemented/diverged/unchanged
    │       └─ Write: decision_validations + update decisions.text for 'updated'
    │
    ├─ Phase B: Module Re-validation
    │   └─ For each eligible module (parallel, MAX_WORKERS=5):
    │       ├─ Collect current component decisions (post-Phase A)
    │       ├─ Read existing module decisions
    │       ├─ Pipeline decisions → LLM comparison → confirmed/updated/outdated/new
    │       ├─ Human decisions → LLM implementation check → implemented/diverged/unchanged
    │       └─ Write: decision_validations + update decisions.text for 'updated'
    │
    ├─ create_map_version(trigger='revalidation')     → after_version_id
    ├─ complete_validation_run(val_run_id, after_version_id)
    └─ complete_pipeline_run(run_id, 'completed')
```

```
Version Comparison:
    │
    ├─ Fetch version_decisions for version A and version B
    ├─ Match by decision_id
    ├─ Classify: added (in B not A), removed (in A not B), changed (text differs), unchanged
    └─ Return: {added, removed, changed, unchanged_count}
```

#### Key Components

| Component | File | Purpose |
|-----------|------|---------|
| Pipeline orchestrator | `backend/revalidation/pipeline.py` | Coordinates before/after snapshots + Phase 0/A/B |
| New file classifier | `backend/revalidation/new_file_classifier.py` | Detects untracked files (disk vs DB diff), LLM classification into components |
| Component revalidator | `backend/revalidation/component_revalidator.py` | Reads code + decisions, LLM comparison, parallel execution |
| Module revalidator | `backend/revalidation/module_revalidator.py` | Reads component decisions + module decisions, LLM comparison |
| Prompts | `backend/revalidation/prompts.py` | 4 LLM prompts: new file classification, pipeline/human/module validation |
| DB functions | `backend/db.py` | ~12 new functions for versioning and validation CRUD |
| API endpoints | `backend/main.py` | Streaming revalidation + version CRUD + validation summary |
| Purple highlighting | `frontend/src/components/graph/` | MapNode, MapView, DetailPanel, graph.css |
| Version panel | `frontend/src/components/graph/VersionPanel.tsx` | Browse/compare versions UI |

---

### Implementation

#### Backend Files

**`backend/revalidation/pipeline.py`** — Orchestrator following `map_descriptions/pipeline.py` pattern. Runs Phase 0 → Phase A → Phase B.

**`backend/revalidation/new_file_classifier.py`** — Phase 0. Two-stage:
1. **Detection (no LLM):** Walk module directories on disk, query `component_files` for all tracked paths, diff to find new files. Uses `test_filter.is_test_file()` to separate source from test. Groups by module via directory prefix matching. Logs orphans (files outside all module directories).
2. **Classification (LLM):** For each module with new files, read file contents and existing components (name, purpose, file list). LLM assigns each file to an existing component or proposes a new component. Writes `component_files` rows and optionally new `components` rows. Test files assigned using same fallback as Part 2: if a new test file's corresponding source file was classified, assign to same component.

Reuses: `BINARY_EXTENSIONS` from `component_describer.py`, `is_test_file` from `test_filter.py`, `_read_component_source`/`_format_file_contents` from `component_describer.py`.

**`backend/revalidation/component_revalidator.py`** — Reuses `_read_component_source`, `_chunk_files`, `_format_file_contents` from `component_describer.py`. ThreadPoolExecutor with MAX_WORKERS=5.

**`backend/revalidation/module_revalidator.py`** — Reuses `_collect_component_decisions`, `_format_component_decisions` from `module_describer.py`. Same parallelism pattern.

**`backend/revalidation/prompts.py`** — Four prompts:
1. **New file classification** (new file contents + existing components → assign to component or propose new)
2. Component pipeline-decision revalidation (code + decisions → confirmed/updated/outdated/new)
3. Human decision implementation check (code + human decisions → implemented/diverged/unchanged)
4. Module decision revalidation (component decisions + module decisions → confirmed/updated/outdated/new)

#### Backend API

```
# Revalidation (streamed via existing /api/run/stream)
step="revalidation" → triggers run_revalidation_pipeline

# Versions
GET  /api/versions                              — list all versions
GET  /api/versions/{id}                         — version detail with decisions
POST /api/versions                              — create manual snapshot
GET  /api/versions/{a_id}/compare/{b_id}        — diff two versions

# Validation
GET  /api/validation-runs                       — list all runs
GET  /api/validation-runs/{id}                  — run detail with outcomes
GET  /api/validation/summary                    — latest results for purple highlighting
```

#### Frontend Files

**Types** (`types.ts`): MapVersion, VersionDecision, VersionComparison, DecisionValidation, ValidationSummary

**API client** (`client.ts`): fetchVersions, fetchVersion, createManualVersion, compareVersions, fetchValidationRuns, fetchValidationRun, fetchValidationSummary

**Highlighting** (`graph.css`, `MapNode.tsx`, `MapView.tsx`): Purple `.has-revalidation` class, `hasRevalidation` flag, `revalidatedNodeIds` Set

**Detail panel** (`DetailPanel.tsx`): Validation badges per decision (updated=purple, outdated=red, implemented=green, diverged=amber)

**Version panel** (`VersionPanel.tsx`): Bottom slide-up with version list, detail, comparison views

**Sidebar** (`MapSidebar.tsx`): History section with "Browse Versions" and "Save Snapshot"

**Launcher** (`App.tsx`): "Re-validate" step in STEPS array

---

### Key Technical Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Separate version tables (not modifying decisions) | Additive snapshot model | Preserves original data, enables historical comparison, keeps decisions table clean |
| No change_records from re-validation | Explicit ticket isolation | Re-validation is map corrections only; ticket pipeline reads change_records exclusively |
| Denormalized names in version_decisions | Snapshot independence | Module/component names survive deletion from Part 1 re-runs |
| Before/after snapshot pairs for re-validation | Clear versioning | Users can see exactly what changed in each re-validation run |
| Reuse component_describer.py helpers | DRY | File reading, chunking, formatting logic is identical |
| Conservative LLM prompts | Prevent false positives | "Only flag as changed if there's a concrete factual difference" |
| Human decisions annotated not modified | User intent preservation | Human edits represent intentional changes; re-validation informs, doesn't override |
| VersionPanel follows TicketPanel pattern | Consistent UX | Bottom slide-up panel is established pattern in this codebase |
| New file detection is pure disk-vs-DB diff (no LLM) | Deterministic, fast | File existence is a factual check; LLM only needed for semantic classification |
| Orphan files (no module match) logged, not auto-assigned | Conservative | Creating new modules is a Part 1 concern; revalidation shouldn't overstep |
| New components from Phase 0 get no decisions yet | Clean handoff | Phase A will immediately analyze them and generate decisions in the same run |

---

## Planned Changes

- [x] Add 4 new tables to `backend/db.py` schema
- [x] Add ~12 new db.py functions for versioning and validation
- [x] Add `create_map_version` call after Part 3 completes
- [x] Create `backend/revalidation/` package (pipeline, component_revalidator, module_revalidator, prompts)
- [x] Add revalidation stream step + version/validation API endpoints to `main.py`
- [x] Add frontend types, API client functions
- [x] Add purple highlighting CSS + MapNode/MapView/DetailPanel changes
- [x] Create VersionPanel.tsx with version list/detail/comparison views
- [x] Add History section to MapSidebar + revalidation to App.tsx STEPS
- [x] Create `backend/revalidation/new_file_classifier.py` — disk walk, DB diff, LLM classification
- [x] Add new file classification prompt to `backend/revalidation/prompts.py`
- [x] Add `db.get_all_component_file_paths()` helper to `backend/db.py`
- [x] Integrate Phase 0 call into `backend/revalidation/pipeline.py` (before Phase A)

---

## Log

- 2026-02-19 :: william :: Created feature documentation. Planned full re-validation pipeline with map versioning.
- 2026-02-19 :: william :: Implemented full re-validation pipeline and map versioning. Backend: 4 new DB tables (map_versions, version_decisions, validation_runs, decision_validations), 12 new db.py functions, revalidation package (pipeline.py, component_revalidator.py, module_revalidator.py, prompts.py), auto-snapshot after Part 3, 7 new API endpoints, revalidation streaming step. Frontend: 6 new types, 7 new API client functions, purple highlighting (CSS + MapNode + MapView + DetailPanel validation badges), VersionPanel.tsx (list/detail/compare views), History section in MapSidebar, revalidation step in App.tsx launcher.
- 2026-02-19 :: william :: Added Phase 0 (new file classification) to revalidation pipeline. Detection is pure disk-vs-DB diff (no LLM): walks module directories, diffs against component_files table. Classification uses LLM to assign new files to existing components or propose new ones. Files: new_file_classifier.py, new prompt in prompts.py, get_all_component_file_paths() in db.py, Phase 0 call in pipeline.py before Phase A.
