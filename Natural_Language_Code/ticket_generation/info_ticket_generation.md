# Ticket Generation

> Part of [Legend Architecture Map](../legend_map/info_legend_map.md)

## Section 1 (HUMAN EDITS ONLY)

### Purpose

Convert accumulated map changes into implementation tickets that a developer (or AI agent) can pick up and execute. Collects all change records since the last baseline — regardless of whether they originated from human direct edits or approved AI suggestions — and produces self-contained work items describing what to change, where, and what "done" looks like.

---

### How it Works

#### Workflow

1. **Collect change records** — Gather all change records since the last baseline from the `change_records` table. This includes human direct edits and (in V2) approved AI suggestions — the system treats them identically.

2. **Net-delta collapsing** — For each decision that was touched multiple times, collapse the sequence into a single net change (first old value → last new value). If edits cancel out (A→B→A), the change is dropped entirely.

   **Component change collapsing rules:**
   - `add` + `remove` = cancel (drop both)
   - `add` + `edit` = net `add` (with final values)
   - `edit` + `remove` = net `remove` (with original old values)
   - Multiple `edit`s = first old → last new (same as decision collapsing)

3. **Map correction vs code change classification** — For each net change, the LLM reads the relevant source code and classifies:
   - **Map correction** — The code already matches the new decision; the map was simply wrong or outdated. No code work needed.
   - **Code change** — The code does not yet match the new decision; implementation work is required.

4. **Group related changes** — Code changes are grouped into tickets:
   - **Same component + related concern** → one ticket (e.g., two decisions about error handling in the same component)
   - **Cross-component dependency** → one ticket (e.g., a new API endpoint in one component requires a client update in another)
   - **Independent changes** → separate tickets

5. **LLM generates tickets** — For each group, the LLM produces a ticket with:
   - **Title** — Short, actionable (e.g., "Add JWT token caching to auth component")
   - **Description** — What needs to change and why, with enough context to act on without external references
   - **Source decisions** — The decision IDs that triggered this ticket, for traceability
   - **Affected files** — Specific file paths from the component(s)
   - **Acceptance criteria** — How to verify the change is complete (testable conditions)

   Each ticket is designed to be self-contained enough to copy-paste into an AI coding agent as a task prompt.

   **DB write sequence per ticket:**
   ```
   add_ticket(title, description, acceptance_criteria) -> ticket_id
   add_ticket_files(ticket_id, [file_paths])
   link_ticket_change_records(ticket_id, [change_record_ids])
   ```
   Note: `add_ticket_decisions()` exists in `db.py` but is not currently called — traceability to decisions is indirect via change records. Direct decision linking may be added later.

6. **Baseline advancement** — A new baseline is created via `create_baseline()`. The diff view resets. Subsequent changes accumulate from here.

7. **Output** — Tickets are presented as a displayable list in the UI and exportable as markdown. Map corrections are reported to the human (e.g., "3 changes were map corrections — no tickets needed") but do not produce tickets. Tickets are linked to their source change records via the `ticket_change_records` join table.

#### Rules & Edge Cases

- Tickets reference the architecture map decisions they implement via decision IDs — traceability goes both ways
- If all changes since the last baseline are map corrections, no tickets are generated but the baseline still advances — the human is informed
- If no changes exist since the last baseline, "Generate Tickets" is a no-op
- Tickets should not assume the implementer has context beyond what's in the ticket — include file paths, the relevant decision text, and enough description to act on

---

## Section 2 (HUMAN + AI)

### Architecture

#### Backend API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/tickets/generate` | `POST` | Reads change records since baseline, runs LLM, writes tickets to DB, advances baseline |
| `/api/tickets` | `GET` | Returns all saved tickets with files and linked decision IDs |

**POST /api/tickets/generate — flow:**

```
1. get_current_baseline(conn)
2. get_change_records(conn, since_baseline_id=baseline['id'])
3. If no records → return { tickets: [], map_corrections: 0, message: "No changes since last baseline" }
4. Net-delta collapse: group records by entity_id, reduce sequences into single net change
5. Send collapsed changes to LLM → get ticket list back
6. For each ticket:
     add_ticket(title, description, acceptance_criteria) → ticket_id
     add_ticket_files(ticket_id, file_paths)
     link_ticket_change_records(ticket_id, change_record_ids)
7. create_baseline(conn) — advance the baseline
8. Return { tickets: [...], map_corrections: N }
```

**GET /api/tickets — response:**

```json
{
  "tickets": [
    {
      "id": 1,
      "title": "Switch DataLayer to async DB calls",
      "description": "The architecture map now specifies async DB calls with connection pooling...",
      "acceptance_criteria": "All DB calls in DataLayer use async/await. Connection pool configured.",
      "status": "open",
      "files": ["backend/db.py", "backend/components/data_layer.py"],
      "decision_ids": [7, 12],
      "created_at": "2026-02-16T12:00:00"
    }
  ]
}
```

#### LLM Prompt Structure

The LLM receives the net-collapsed change records and the relevant component context, and returns a list of tickets.

**System prompt:**
```
You are converting architecture map changes into implementation tickets.
Each ticket must be self-contained — include enough context that a developer can act on it without external references.
Respond with a JSON array of ticket objects.
```

**User prompt (constructed from net-collapsed records):**
```
Architecture map changes since last baseline:

1. EDIT decision on component "DataLayer" (module: Backend)
   Category: patterns
   Was: "Uses synchronous DB calls via psycopg2"
   Now: "Uses async DB calls with connection pooling via asyncpg"

2. ADD decision on component "AuthHandler" (module: Backend)
   Category: error_handling
   Now: "Returns 401 with WWW-Authenticate header on invalid token"

For each change that requires code implementation, produce a ticket.
If the code already matches the change (map correction), set is_map_correction: true.

Return JSON array:
[
  {
    "title": "...",
    "description": "...",
    "acceptance_criteria": "...",
    "affected_files": ["..."],
    "change_record_ids": [1, 2],
    "is_map_correction": false
  }
]
```

The LLM may group multiple changes into one ticket when they are related (same component, same concern). Independent changes become separate tickets.

#### Net-Delta Collapsing

Before sending to LLM, collapse multiple records for the same entity:

| Sequence | Result |
|----------|--------|
| `add` + `remove` | Cancel — drop both |
| `add` + `edit` | Net `add` with final new value |
| `edit` + `remove` | Net `remove` with original old value |
| `edit` + `edit` | Single `edit`: first old → last new |
| `edit` → back to original | Drop (no net change) |

#### System Flow

```
Human clicks "Generate Tickets"
        ↓
Frontend: POST /api/tickets/generate
        ↓
main.py: get change records → net-delta collapse → LLM prompt
        ↓
LLM: returns JSON ticket list
        ↓
main.py: write tickets to DB → create_baseline()
        ↓
Response: { tickets: [...], map_corrections: N }
        ↓
Frontend: open TicketPanel, display ticket list with copy/download buttons
Diff badges reset (new baseline)

Human clicks "View Tickets"
        ↓
Frontend: GET /api/tickets → map SavedTicket to GeneratedTicket format
        ↓
Frontend: open TicketPanel, display saved ticket list with copy/download buttons
```

#### Key Components

| File | Responsibility |
|------|----------------|
| `backend/main.py` | `POST /api/tickets/generate` and `GET /api/tickets` endpoints |
| `backend/db.py` | `add_ticket`, `add_ticket_files`, `link_ticket_change_records`, `create_baseline`, `get_tickets` |
| `frontend/src/components/graph/TicketPanel.tsx` | Sliding panel: ticket list, copy-as-markdown per ticket, download-as-markdown, map correction count |
| `frontend/src/api/client.ts` | `generateTickets()`, `fetchTickets()` |

---

### Implementation

#### Files

- `backend/main.py` — add `POST /api/tickets/generate` and `GET /api/tickets`
- `backend/db.py` — already implemented (add_ticket, link_ticket_change_records, create_baseline, get_tickets)
- `frontend/src/components/graph/TicketPanel.tsx` — new component
- `frontend/src/api/client.ts` — add generateTickets() and fetchTickets()

#### Ticket Markdown Format (for copy button)

```markdown
## Switch DataLayer to async DB calls

**Description:**
The architecture map now specifies async DB calls with connection pooling...

**Acceptance Criteria:**
- All DB calls in DataLayer use async/await
- Connection pool configured

**Affected Files:**
- `backend/db.py`
- `backend/components/data_layer.py`
```

---

### Key Technical Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Net-delta collapse before LLM | Collapse in Python, not by LLM | Deterministic, cheap, avoids sending noise (e.g. A→B→A) to LLM |
| LLM groups related changes | LLM decides grouping, not rules | Grouping logic is context-dependent; LLM reads entity names and categories to judge relatedness |
| Baseline advances after generation | Always, even if some are map corrections | Prevents double-generation. Map corrections are reported but don't block advancement. |
| Tickets stored in DB | Persisted, not ephemeral | Allows `GET /api/tickets` to return history, status tracking, and traceability links to change records |

---

## Planned Changes

- [x] Add `POST /api/tickets/generate` to `backend/main.py`
- [x] Add `GET /api/tickets` to `backend/main.py`
- [x] Implement net-delta collapse in `main.py` (Python, before LLM call)
- [x] Build LLM prompt from collapsed change records
- [x] Build `TicketPanel.tsx` with copy-as-markdown per ticket
- [x] Add `generateTickets()` and `fetchTickets()` to `api/client.ts`
- [x] Add "View Tickets" button to sidebar — fetches saved tickets from DB and shows in TicketPanel
- [x] Add "Download" button to TicketPanel header — exports all tickets as a `.md` file

---

## Log

- 2026-02-16 :: william :: Created doc (Section 1 draft)
- 2026-02-16 :: william :: Rewrote Section 1 — tickets now come from all accumulated change records (not just AI suggestions); added net-delta collapsing, map correction classification, baseline lifecycle
- 2026-02-16 :: william :: NLC audit: Moved from research_agent/ to ticket_generation/ (independent folder). Updated Part-of link to legend_map. Added DB table references (change_records, baselines, ticket_change_records)
- 2026-02-16 :: william :: NLC audit fix: Added decision traceability via ticket_decisions join table, added DB write sequence per ticket, defined component change collapsing rules
- 2026-02-16 :: william :: Filled Section 2: backend API spec, LLM prompt structure, net-delta collapse table, system flow, key technical decisions. Implemented POST /api/tickets/generate (net-delta collapse + Anthropic call + DB write + baseline advance) and GET /api/tickets in main.py.
- 2026-02-16 :: william :: Implemented frontend: TicketPanel.tsx (slides up from bottom of canvas, ticket cards with copy-as-markdown, map corrections count, Copy All button). generateTickets() in api/client.ts. MapSidebar "Generate Tickets" button with change count badge. MapView wires generating state + ticket state + baseline refresh after generation.
- 2026-02-18 :: william :: Doc sync: Updated DB write sequence — add_ticket_decisions() is not called in current implementation. Traceability to decisions is indirect via change records.
- 2026-02-18 :: william :: Added backend test suite (backend/tests/). test_collapse_tickets.py covers _collapse_changes() net-delta logic and _build_ticket_prompt() (16 tests).
- 2026-02-20 :: william :: Added "View Tickets" button to sidebar (fetches saved tickets via GET /api/tickets, maps SavedTicket→GeneratedTicket, opens TicketPanel). Added "Download" button to TicketPanel header (exports all tickets as timestamped .md file using blob download).
