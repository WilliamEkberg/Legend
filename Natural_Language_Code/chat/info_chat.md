# Chat (MCP-based Architecture Assistant)

> Part of [Legend Architecture Map](../legend_map/info_legend_map.md)

## Section 1 (HUMAN EDITS ONLY)

### Purpose

A conversational interface that lets users query and modify the architecture map through natural language. The chat uses an MCP (Model Context Protocol) server over stdio to expose graph data as tools that an LLM can call, enabling questions like "what depends on the Auth module?" and edit requests like "add a caching decision to the API service."

---

### How it Works

#### Two Modes (like Cursor)

**Ask mode** — Read-only. The LLM can query modules, components, decisions, edges, and statistics but cannot modify the map. Use this to explore and understand the architecture.

**Edit mode** — Read + write. The LLM can propose changes (add/edit/delete modules, components, decisions, edges). All write operations are **intercepted and proposed to the user for confirmation** before being applied. The user reviews each proposed change and clicks Apply or Reject.

#### Workflow

1. User opens the chat sidebar on the map view and selects Ask or Edit mode
2. User types a message (e.g., "What are the dependencies of the Backend module?")
3. The backend calls the LLM (via litellm, same multi-provider setup as the pipeline) with MCP tools
4. The LLM calls tools to query the graph (e.g., `get_module_edges`), receives results, and formulates an answer
5. The answer streams to the user as SSE events, with tool call indicators visible
6. In Edit mode: write tool calls are intercepted → proposed changes appear as cards → user applies or rejects

#### Propose-Then-Confirm (Edit Mode)

When the LLM calls a write tool in edit mode:
- The write is NOT executed immediately
- A ProposedChange card appears in the chat showing what would change
- The LLM receives a synthetic "awaiting confirmation" result and continues its response
- The user clicks Apply (executes the write via MCP) or Reject (discards it)
- Applied changes create change records with `origin: 'chat'` and trigger a map refresh

#### Rules & Edge Cases

- API key, provider, and model are read from localStorage (same settings as the pipeline launcher)
- If no API key is set, the chat shows a message to set one in the launcher
- Sessions are in-memory (no persistence across server restarts) — the app runs locally as a single-user Tauri desktop app
- The MCP server connects to the same `legend.db` as the backend
- Node references in LLM responses (formatted as `[Name](module:ID)`) are clickable and navigate to that node on the map
- The chat does not interfere with manual map editing — both can be used simultaneously
- Change records from chat have `origin: 'chat'` to distinguish from `origin: 'human'` (manual edits)

---

## Section 2 (HUMAN + AI)

### Architecture

#### System Flow

```
Frontend (ChatPanel sidebar)
    |  POST /api/chat (SSE stream)
    v
Backend (FastAPI — chat.py)
    |  Spawns subprocess, communicates over stdio
    v
MCP Server (mcp_server.py — separate Python process)
    |  SQLite queries via db.py
    v
legend.db
```

#### MCP Server Tools

**Read tools** (available in both modes):

| Tool | Params | Description |
|------|--------|-------------|
| `get_full_map` | — | Full map: modules, components, decisions, edges |
| `get_module` | `module_id: int` | Single module with components + decisions + directories |
| `get_component` | `component_id: int` | Single component with files + decisions |
| `get_decisions` | `module_id?: int, component_id?: int` | Filtered decisions |
| `get_module_edges` | `source_id?: int` | Module-level dependency edges |
| `get_component_edges` | `source_id?: int` | Component-level dependency edges |
| `search_entities` | `query: str` | Full-text search across module names, component names, decision text |
| `get_change_records` | — | Pending changes since last baseline |
| `get_statistics` | — | Counts of modules, components, decisions, edges |

**Write tools** (Edit mode only — intercepted for propose-then-confirm):

| Tool | Params | Description |
|------|--------|-------------|
| `add_module` | `name, classification?, type?, technology?` | Create a new module |
| `add_component` | `module_id, name, purpose?` | Create a component within a module |
| `add_decision` | `text, category, module_id?, component_id?` | Add a technical decision |
| `update_decision` | `decision_id, text?, category?` | Edit a decision's text or category |
| `delete_decision` | `decision_id` | Remove a decision |
| `add_module_edge` | `source_id, target_id, edge_type, label?` | Create module-level edge |
| `add_component_edge` | `source_id, target_id, edge_type, label?` | Create component-level edge |
| `delete_module` | `module_id` | Delete module (cascades) |
| `delete_component` | `component_id` | Delete component (cascades) |

All write tools reuse existing CRUD functions from `db.py` and create change records with `origin: 'chat'`.

#### Chat Orchestration (chat.py)

- **MCP client**: Spawns `mcp_server.py` as a subprocess, connects over stdio using the `mcp` Python SDK
- **LLM loop**: Calls `litellm.acompletion()` with MCP tools converted to OpenAI function format. Supports multi-turn tool calling (up to 10 iterations per message)
- **Mode filtering**: In Ask mode, write tools are excluded from the tool list. In Edit mode, all tools are available but writes are intercepted
- **Sessions**: In-memory `ChatSession` objects storing conversation history and pending proposed changes. Keyed by UUID session IDs
- **Streaming**: Yields SSE event dicts as an async generator. Event types: `text`, `tool_call`, `tool_result`, `proposed_change`, `error`, `done`

#### Backend API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/chat` | `POST` | SSE streaming chat. Accepts `{message, mode, session_id?, api_key, provider, model?}`. Streams events to the frontend |
| `/api/chat/confirm` | `POST` | Apply proposed changes. Accepts `{session_id, change_ids[]}`. Executes stored write tool calls via MCP |
| `/api/chat/session/{id}` | `DELETE` | Clear conversation history for a session |

**SSE event types:**

```
{type: "text", content: "..."} — LLM text chunk (streaming)
{type: "tool_call", name: "...", arguments: {...}} — Tool being called
{type: "tool_result", name: "...", result: "..."} — Tool result (read tools only)
{type: "proposed_change", change: {id, tool_name, arguments, description, status}} — Edit mode proposal
{type: "error", text: "..."} — Error
{type: "done", session_id: "...", proposed_changes: [...]} — Stream complete
```

#### Frontend (ChatPanel)

420px collapsible right sidebar on the map view. Uses the same styling patterns as DetailPanel (framer-motion slide, shadcn Button, Tailwind utilities).

Features:
- **Mode toggle**: Ask / Edit toggle in header (styled like the L2/L3 toggle)
- **Streaming**: Text streams in real-time via SSE (same pattern as `runOpenCodeStream`)
- **Markdown rendering**: Assistant messages rendered with `react-markdown`. Node references (`[Name](module:ID)`) parsed into clickable links that navigate to the node on the map
- **Tool call badges**: Blue pill badges showing which tools the LLM called
- **ProposedChange cards**: Color-coded cards (green=add, amber=edit, red=delete) with per-change Apply/Reject buttons
- **Apply All / Reject All**: Summary bar when multiple changes are pending
- **Map refresh**: After applying changes, triggers `refreshMap()` and `refreshChangeRecords()` via `onMapMutated` callback

#### Key Components

| File | Responsibility |
|------|----------------|
| `backend/mcp_server.py` | Standalone MCP server over stdio — 9 read tools + 9 write tools, reuses `db.py` CRUD |
| `backend/chat.py` | Chat orchestration — MCP client management, LLM conversation loop, sessions, propose-then-confirm |
| `backend/main.py` | 3 new endpoints: `/api/chat`, `/api/chat/confirm`, `/api/chat/session/{id}` |
| `frontend/src/components/graph/ChatPanel.tsx` | Chat sidebar UI — messages, streaming, mode toggle, proposed changes |
| `frontend/src/api/client.ts` | `sendChatMessage()`, `confirmChatChanges()`, `clearChatSession()` |
| `frontend/src/data/types.ts` | `ChatMessage`, `ChatMode`, `ProposedChange`, `ChatEvent` types |
| `frontend/src/components/graph/MapView.tsx` | Chat toggle button, ChatPanel rendering, node navigation callback |

---

### Implementation

#### Files

- `backend/mcp_server.py` — MCP server (read + write tools over stdio)
- `backend/chat.py` — Chat orchestration (MCP client, LLM loop, sessions)
- `backend/main.py` — Chat endpoints added after existing validation section
- `frontend/src/components/graph/ChatPanel.tsx` — Chat sidebar component
- `frontend/src/api/client.ts` — Chat API functions
- `frontend/src/data/types.ts` — Chat type definitions
- `frontend/src/components/graph/MapView.tsx` — Chat integration (toggle button, panel rendering, node navigation)

#### Dependencies

- Backend: `mcp>=1.0` (MCP Python SDK for server + client)
- Frontend: `react-markdown` (markdown rendering for assistant messages)

---

### Key Technical Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| MCP over stdio | Separate process + JSON-RPC over stdin/stdout | Standard protocol — the MCP server can be reused by other MCP clients (Claude Desktop, etc.). Clean separation from the FastAPI backend |
| Propose-then-confirm | Write tools intercepted, not auto-applied | Prevents LLM hallucinations from corrupting the database. User always has final say. Matches Cursor/Copilot UX pattern |
| In-memory sessions | No persistent session storage | App is single-user (Tauri desktop). Chat history is ephemeral — can be rebuilt. No need for DB persistence |
| SSE (not WebSocket) | Server-Sent Events for streaming | Matches existing `/api/run/stream` pattern. Simpler than WebSocket. Client sends messages via POST, so bidirectional is unnecessary |
| `origin: 'chat'` | Distinct from `origin: 'human'` | Distinguishes AI-assisted edits from manual UI edits in change records. Both are treated equally by ticket generation |
| litellm multi-provider | Same setup as pipeline | Users keep their existing API key + provider + model selection. No additional configuration needed |
| Tool call transparency | Tool calls streamed to frontend | Users see what data the LLM accesses. Builds trust and helps debugging. Collapsed as badges to avoid clutter |

---

## Planned Changes

- [x] Create `backend/mcp_server.py` with 9 read tools + 9 write tools over stdio
- [x] Create `backend/chat.py` with MCP client, LLM loop, sessions, propose-then-confirm
- [x] Add 3 chat endpoints to `backend/main.py` (POST /api/chat, POST /api/chat/confirm, DELETE /api/chat/session/{id})
- [x] Add `mcp>=1.0` to `backend/requirements.txt`
- [x] Add `ChatMessage`, `ChatMode`, `ProposedChange`, `ChatEvent` types to `frontend/src/data/types.ts`
- [x] Add `sendChatMessage()`, `confirmChatChanges()`, `clearChatSession()` to `frontend/src/api/client.ts`
- [x] Create `frontend/src/components/graph/ChatPanel.tsx` (sidebar with streaming, mode toggle, proposed changes)
- [x] Install `react-markdown` dependency
- [x] Integrate ChatPanel into MapView (toggle button, panel rendering, node navigation, map refresh)

---

## Log

- 2026-03-02 :: william :: Created doc. Implemented full MCP chat feature: MCP server (mcp_server.py), chat orchestration (chat.py), 3 backend endpoints, ChatPanel frontend component with ask/edit modes, propose-then-confirm flow, streaming SSE, markdown rendering, node navigation. New dependencies: mcp>=1.0 (backend), react-markdown (frontend).
