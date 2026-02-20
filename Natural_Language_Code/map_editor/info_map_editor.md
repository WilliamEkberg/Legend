# Map Editor

> Part of [Legend Architecture Map](../legend_map/info_legend_map.md)

## Section 1 (HUMAN EDITS ONLY)

### Purpose

Allows humans to directly add, edit, or remove decisions and components in the architecture map. Every edit is persisted to the database and creates a **change record** so that accumulated changes can later be converted into implementation tickets. No AI is involved here — the human is the sole source of map changes.

---

### How it Works

#### Workflow

**Step 1: Edit the map** — Humans perform CRUD operations on the map directly in the graph UI:

- **Decision-level**: Add, edit, or remove a technical decision on a component or module
- **Module-level**: Add or remove an entire module (removing cascades to all components, edges, decisions)
- **Component-level**: Add or remove a component from a module (removing cascades to files, edges, decisions)
- **Move**: Move a decision between components (tracked as a remove + add pair)

Each operation:
1. Persists the change to the database (`decisions` or `components` table)
2. Creates a **change record** in `change_records` with: entity type, entity ID, action (`add` | `edit` | `remove`), old/new value snapshot, `origin='human'`, and the current baseline ID

**Source protection rule:** When a human edits a pipeline-generated decision, the decision's `source` field is updated from `'pipeline_generated'` to `'human'`. This protects it from being deleted if Part 3 is re-run (`clear_decisions` only removes `pipeline_generated` rows).

**Step 2: Review changes** — A diff panel shows all changes since the last baseline:

- New decisions: green left border, "+" badge
- Edited decisions: amber left border, "~" badge, old text as strikethrough + new text bold
- Removed decisions: red left border, "-" badge

**Step 3: Generate tickets** — Once changes look good, human clicks "Generate Tickets." The ticket generation process reads all change records since the current baseline and an AI produces implementation tickets. See [Ticket Generation](../ticket_generation/info_ticket_generation.md).

---

### Component CRUD Detail

When adding or removing components, cascading effects apply:

- **Add component**: Create component row, assign files (moved from another component or new), add component_files entries.
- **Remove component**: Removing cascades to component_files, L3 edges, and decisions. Each removed decision creates its own change record.
- **Move files between components**: Update component_files entries.

---

### Rules & Edge Cases

- Humans can edit the map at any time — no session locks or required sequences
- Moving a decision between components is displayed as a move, not separate add/remove
- `origin` is always `'human'` for all edits made through the UI

---

## Section 2 (HUMAN + AI)

### Architecture

#### Backend API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/decisions/{id}` | `PATCH` | Edit decision text and/or category. Reads old state → writes new → creates `edit` change record |
| `/api/decisions` | `POST` | Add new decision to a component or module. Writes row → creates `add` change record |
| `/api/decisions/{id}` | `DELETE` | Remove a decision. Reads old state → deletes row → creates `remove` change record |
| `/api/change-records` | `GET` | Returns all change records since current baseline, enriched with entity context (component/module name) |
| `/api/modules` | `POST` | Create a new module node (name, classification, type, technology) |
| `/api/components` | `POST` | Create a new component node (module_id, name, purpose) |
| `/api/module-edges` | `POST` | Create an edge between two modules (source_id, target_id, edge_type, label). Returns 409 on duplicate. |
| `/api/component-edges` | `POST` | Create an edge between two components (source_id, target_id, edge_type, label). Returns 409 on duplicate. |
| `/api/modules/{id}` | `DELETE` | Remove a module. Reads old state → deletes row (cascades to components, edges, decisions) → creates `remove` change record |
| `/api/components/{id}` | `DELETE` | Remove a component. Reads old state → deletes row (cascades to files, edges, decisions) → creates `remove` change record |

**Change record creation pattern** (used in all three mutation endpoints):

```python
current_baseline = db.get_current_baseline(conn)
baseline_id = current_baseline['id'] if current_baseline else None

db.add_change_record(
    conn,
    entity_type='decision',
    entity_id=decision_id,
    action='edit',           # or 'add' / 'remove'
    old_value=json.dumps(old_state),   # None for 'add'
    new_value=json.dumps(new_state),   # None for 'remove'
    origin='human',
    baseline_id=baseline_id
)
```

`source` field: always `'human'` on mutation (not `'user_edited'`). This aligns with `clear_decisions(source='pipeline_generated')` which only clears pipeline-generated rows.

#### Request / Response Shapes

**PATCH /api/decisions/{id}**
```json
Request:  { "text": "...", "category": "..." }   // either or both
Response: { "ok": true }
```

**POST /api/decisions**
```json
Request:  { "text": "...", "category": "...", "component_id": 1 }
          // or "module_id" instead of "component_id"
Response: { "id": 42 }
```

**DELETE /api/decisions/{id}**
```json
Response: { "ok": true }
```

**DELETE /api/modules/{id}**
```json
Response: { "ok": true }
```

**DELETE /api/components/{id}**
```json
Response: { "ok": true }
```

**GET /api/change-records**
```json
Response: {
  "baseline_id": 3,
  "records": [
    {
      "id": 12,
      "entity_type": "decision",
      "entity_id": 7,
      "action": "edit",
      "old_value": { "category": "patterns", "text": "Uses sync DB calls" },
      "new_value": { "category": "patterns", "text": "Uses async DB calls" },
      "origin": "human",
      "created_at": "2026-02-16T12:00:00",
      "context": { "component_name": "DataLayer", "module_name": "Backend" }
    }
  ]
}
```

The `context` field is joined server-side so the frontend doesn't need to look up names.

#### System Flow

```
Human clicks edit/add/delete in DetailPanel
        ↓
Frontend calls PATCH | POST | DELETE /api/decisions
        ↓
main.py: read old state → mutate DB → add_change_record(origin='human')
        ↓
Frontend: re-fetches GET /api/change-records
        ↓
DetailPanel: overlays +/~/- diff badges on affected decisions
        ↓
Human clicks "Generate Tickets" → ticket_generation pipeline
```

#### Key Components

| File | Responsibility |
|------|----------------|
| `backend/main.py` | PATCH/POST/DELETE /api/decisions, DELETE /api/modules/{id}, DELETE /api/components/{id}, GET /api/change-records |
| `backend/db.py` | `add_change_record`, `get_change_records`, `update_decision`, `delete_decisions_by_ids`, `delete_module`, `delete_component` |
| `frontend/src/components/graph/DetailPanel.tsx` | Inline edit/add/delete on decisions; delete button for module/component; reads change records to overlay diff badges |
| `frontend/src/api/client.ts` | `patchDecision()`, `createDecision()`, `deleteDecision()`, `fetchChangeRecords()`, `createModule()`, `createComponent()`, `deleteModule()`, `deleteComponent()`, `createModuleEdge()`, `createComponentEdge()` |
| `frontend/src/components/graph/CreateNodeModal.tsx` | Modal for creating modules (L2) or components (L3) |
| `frontend/src/components/graph/EdgeLabelPopup.tsx` | Modal for edge type + label after handle drag |

---

### Implementation

#### Files

- `backend/main.py` — add change record calls to existing decision endpoints; add `GET /api/change-records`
- `backend/db.py` — already implemented
- `frontend/src/components/graph/DetailPanel.tsx` — add edit/add/delete controls per decision
- `frontend/src/api/client.ts` — add mutation + change-records fetch functions

---

### Key Technical Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| `source='human'` not `'user_edited'` | Single consistent value | `clear_decisions` checks for `'pipeline_generated'`; any other value is treated as human-protected. Using one value avoids ambiguity. |
| Change records created server-side | Inside the API handler, not client-side | Ensures every mutation is recorded even if the client crashes or retries. Atomic with the DB write. |
| `context` field in GET /api/change-records | Join server-side | Frontend shouldn't need to reconstruct component/module names. Simpler display logic. |
| Diff badges read from change-records response | Not inferred from local state | Single source of truth. If the user reloads the page the diff state is preserved from the DB. |

---

## Planned Changes

- [x] Fix `PATCH /api/decisions/{id}`: add `add_change_record` call, change source to `'human'`
- [x] Fix `POST /api/decisions`: add `add_change_record` call, change source to `'human'`
- [x] Fix `DELETE /api/decisions/{id}`: read old state first, add `add_change_record` call
- [x] Add `GET /api/change-records` endpoint
- [x] Add inline edit/add/delete controls to `DetailPanel.tsx`
- [x] Overlay +/~/- diff badges on decisions in `DetailPanel.tsx`
- [x] Add `DELETE /api/modules/{id}` endpoint with change record
- [x] Add `DELETE /api/components/{id}` endpoint with change record
- [x] Add delete button to DetailPanel header for modules and components
- [x] Add `deleteModule()` and `deleteComponent()` to `client.ts`

---

## Log

- 2026-02-16 :: william :: Created doc as "Research Agent" (Section 1 draft)
- 2026-02-16 :: william :: Rewrote Section 1 — replaced AI scanning model with human-edit-first model
- 2026-02-16 :: william :: NLC audit: Removed ticket generation (now its own folder). Marked AI suggestions as V2/future.
- 2026-02-16 :: william :: NLC audit fix: Standardized action enum to 'add'|'edit'|'remove', added source update rule
- 2026-02-16 :: william :: Renamed "Research Agent" → "Map Editor". No AI involved here — human edits only. Filled in Section 2: backend API routes, change record pattern, request/response shapes, system flow, key technical decisions. Removed V2 AI Suggestions section (moved to future if ever needed).
- 2026-02-16 :: william :: Implemented backend: fixed PATCH/POST/DELETE /api/decisions to write change records (origin='human', source='human'). Added GET /api/change-records with context enrichment. Added get_decision() to db.py.
- 2026-02-16 :: william :: Implemented frontend: DetailPanel accepts changeRecords prop and overlays +/~/- diff badges (green/amber/red) per decision. onMutate callback triggers refreshChangeRecords in MapView. Added fetchChangeRecords() to api/client.ts.
- 2026-02-19 :: william :: Added node + edge creation: 4 new POST endpoints (modules, components, module-edges, component-edges). Frontend: CreateNodeModal, EdgeLabelPopup, topbar create button, onConnect edge dragging with popup.
- 2026-02-19 :: william :: Added module/component deletion: DELETE /api/modules/{id} and DELETE /api/components/{id} endpoints with change records. Delete button in DetailPanel header with confirmation dialog. Added delete_module() to db.py, deleteModule()/deleteComponent() to client.ts.
