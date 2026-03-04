# OpenCode CLI Web Wrapper

## Section 1 (HUMAN EDITS ONLY)

### Purpose

A desktop/web application that lets users input an API key, select a provider, and trigger the OpenCode CLI (`opencode run`) via a web interface. The backend constructs prompts from configurable prompt modules, injects credentials as environment variables, and runs the CLI as a subprocess.

---

### How it Works

#### Workflow

1. User opens the app (Tauri desktop or web at `localhost:5173`) and enters their API key + selects a provider (Anthropic, OpenAI, etc.)
2. User clicks "Run" — the frontend POSTs `{ api_key, provider, model? }` to `/api/run`
3. Backend builds a prompt (delegated to prompt modules — see [L2 Clustering](./info_opencode_runner_L2_clustering.md)), injects the API key as an env var, and runs `opencode run --agent build -m <model> <prompt>` via subprocess
4. Frontend displays stdout/stderr and success status. Generated files appear in `output/`.

#### Rules & Edge Cases

- API keys are never stored to disk — only passed as subprocess env vars
- Prompts are constructed server-side — not sent by the frontend
- 120s subprocess timeout to prevent hanging
- If `opencode` CLI is not installed, returns a clear error message

---

### Related Documents

- [Legend Architecture Map (pipeline overview)](../legend_map/info_legend_map.md) — This module is Part 1 of the Legend pipeline

### Sub-documents

- [L2 Clustering](./info_opencode_runner_L2_clustering.md) — C4 Level 2 classification prompts, workflow, and output schema

---

## Section 2 (HUMAN + AI)

### Architecture

#### System Flow

**Non-streaming (`/api/run`):**
```
Tauri Desktop / Browser (localhost:5173)   Backend (localhost:8000)              Shell
┌─────────────────────┐                   ┌──────────────────────────┐
│ API Key input        │  POST            │ FastAPI /api/run          │  subprocess.run
│ Provider select      │ ──────>          │                          │ ──────────────>  opencode run --agent build -m provider/model <prompt>
│ [Run] button         │                  │ Builds prompt from       │                  (produces output files)
│                      │ <──────          │ prompt modules           │ <──────────────
│ Output display       │  JSON            │ Returns stdout/stderr    │  exit code + output
└─────────────────────┘                   └──────────────────────────┘
```

**Streaming (`/api/run/stream`) — three-step pipeline:**
```
Browser                         Backend /api/run/stream (SSE)
┌──────────┐  POST step=part1   ┌─────────────────────────────────────────────┐
│ Part 1   │ ─────────────────> │ part1_stream():                             │
│ button   │                    │   launch opencode + SCIP indexer in parallel│
│          │ <── SSE events ─── │   [Part 1] lines from opencode stdout/stderr│
│          │                    │   [SCIP] lines from indexer stdout          │
│          │                    │   on success: ingest JSON → DB              │
└──────────┘                    └─────────────────────────────────────────────┘

┌──────────┐  POST step=part2   ┌─────────────────────────────────────────────┐
│ Part 2   │ ─────────────────> │ part2_stream():                             │
│ button   │                    │   if .scip exists → skip indexer            │
│          │ <── SSE events ─── │   else → run SCIP indexer                  │
│          │                    │   component discovery → write to DB         │
└──────────┘                    └─────────────────────────────────────────────┘
```

SSE event format: `data: {"type": "stdout"|"stderr"|"error"|"done", "text": "..."}`
The `done` event carries `{"type": "done", "success": true|false}`.

#### Key Components

- `backend/main.py` — FastAPI app with single POST `/api/run` endpoint
- `backend/prompts.py` — Prompt construction module (see [L2 Clustering](./info_opencode_runner_L2_clustering.md))
- `backend/requirements.txt` — Python dependencies (fastapi, uvicorn)
- `frontend/src/App.tsx` — React UI with API key input, provider select, run button, output display
- `frontend/src/api/client.ts` — fetch wrapper for `/api/run`
- `frontend/src-tauri/` — Tauri desktop app wrapper (Rust)
- `frontend/vite.config.ts` — Vite dev server with proxy to backend
- `start.sh` — Startup script: creates Python venv, installs deps, launches backend + Tauri app

---

### Implementation

#### Files

- `backend/main.py` — FastAPI app, single endpoint, subprocess execution
- `backend/requirements.txt` — fastapi, uvicorn
- `frontend/` — Vite + React + TypeScript scaffold
- `frontend/src/App.tsx` — Main UI component
- `frontend/src/api/client.ts` — API client
- `frontend/src-tauri/` — Tauri desktop app (Rust)
- `start.sh` — Full-stack startup script (venv + backend + Tauri)
- `output/` — Directory where opencode creates output files

#### Backend API

- `POST /api/run` — Accepts `{ api_key, provider, model? }`, builds prompt server-side, runs opencode CLI, returns `{ success, output, error }`
- `POST /api/run/stream` — Accepts `{ api_key, provider, model?, step, repo_path? }`, streams subprocess output as SSE. `step` is one of:
  - `"part1"` — OpenCode L2 classification (modules + edges steps) + parallel SCIP indexing; ingests output JSON to DB
  - `"part2"` — Component discovery (reuses SCIP file from Part 1 if present, else runs indexer)
  - `"part3"` — Map description generation (component decisions + module elevation)
  - `"edges"` — Re-aggregate component edges from SCIP (standalone re-run of Phase 2 edges)
- `GET /api/map` — Returns full architecture map (modules + edges) as JSON for the graph view

Additional endpoints implemented in `main.py` (documented in their own info files):
- Decision CRUD (`PATCH/POST/DELETE /api/decisions`) — See [Map Editor](../research_agent/info_map_editor.md)
- `GET /api/change-records` — See [Map Editor](../research_agent/info_map_editor.md)
- `POST /api/tickets/generate`, `GET /api/tickets` — See [Ticket Generation](../ticket_generation/info_ticket_generation.md)

#### Helper: `_get_scip_cmd(source_dir)`

Returns `(command_list, description_string)` for the best available SCIP indexer:
1. Docker `scip-engine` image (preferred — all indexers bundled)
2. Local binary: `backend/scip-engine/legend-indexer/target/release/legend-indexer`
3. Shell script: `backend/scip-engine/scripts/analyze-local.sh`

Set `SCIP_LOCAL=1` to force local binary over Docker.

#### Docker SCIP Retry Logic

On macOS, Docker Desktop's VirtioFS can intermittently fail to populate volume mounts, causing the SCIP indexer to see 0 files. The indexer exits with code 1 in this case (see [SCIP Indexing docs](../component_discovery/info_component_discovery_scip_indexing.md)). The backend retries Docker SCIP invocations up to `SCIP_DOCKER_MAX_RETRIES` (3) times with a `SCIP_DOCKER_RETRY_DELAY` (2s) pause between attempts. This applies to both `part1_stream()` and `part2_stream()`. Non-Docker indexers (local binary/script) are not retried.

#### Provider Mapping

The backend maps provider names to environment variable names for API key injection:

| Provider | Env Var |
|----------|---------|
| `anthropic` | `ANTHROPIC_API_KEY` |
| `openai` | `OPENAI_API_KEY` |
| `google` | `GEMINI_API_KEY` |
| `groq` | `GROQ_API_KEY` |

#### Available Models per Provider

The frontend shows a dropdown of models filtered by provider. First model in each list is the default.

| Provider | Models (litellm format) |
|----------|------------------------|
| `anthropic` | `anthropic/claude-sonnet-4-20250514` (default), `anthropic/claude-opus-4-20250514`, `anthropic/claude-haiku-4-5-20251001` |
| `openai` | `openai/gpt-4o` (default), `openai/gpt-4o-mini`, `openai/o3-mini` |
| `google` | `gemini/gemini-2.5-pro`, `gemini/gemini-2.0-flash` |
| `groq` | `groq/llama-3.3-70b-versatile`, `groq/llama-3.1-8b-instant`, `groq/mixtral-8x7b-32768` |

If no model is specified in the API request, backend defaults apply:
- `anthropic` → `anthropic/claude-sonnet-4-20250514`
- `openai` → `openai/gpt-4o`

---

### Key Technical Decisions

#### subprocess.run vs opencode serve

**Choice:** subprocess.run

**Why:** Simpler and stateless. No server lifecycle to manage. Clean upgrade path to `serve` if needed later.

#### CORS vs Vite proxy

**Choice:** Vite proxy

**Why:** No CORS middleware needed. Same-origin requests in dev. Cleaner setup.

#### Prompt constructed server-side

**Choice:** Backend builds the full prompt from prompt modules

**Why:** Keeps prompt logic out of the frontend. Frontend only sends credentials and provider — prompt construction is centralized.

#### Tauri desktop wrapper

**Choice:** Tauri (Rust) for native desktop app

**Why:** Lightweight, secure, reuses the existing Vite/React frontend. Single codebase for web and desktop.

#### Parallel SCIP indexing in Part 1

**Choice:** Launch the SCIP indexer concurrently with the OpenCode agent during Part 1, using `asyncio.Queue` to merge their output streams.

**Why:** SCIP indexing scans the whole codebase and can be slow. It has no dependency on Part 1's JSON output. Running it in parallel means the `.scip` file is ready (or nearly ready) by the time the user clicks "Run Part 2". Part 2 detects the existing file and skips the indexer entirely. SCIP failure during Part 1 is non-fatal — Part 2 falls back to running the indexer itself.

#### Docker SCIP retry on mount failure

**Choice:** Retry Docker SCIP invocations up to 3 times (2s delay) when the indexer exits with code 1 due to an empty volume mount.

**Why:** Docker Desktop for Mac has an intermittent VirtioFS race condition where volume mounts are empty at container start. Without retry, the indexer silently reports 0 files and produces no `.scip` output, causing Part 2 (Component Discovery) to fail. The indexer now exits with code 1 when `total_files == 0`, and both `part1_stream()` and `part2_stream()` retry Docker-based invocations. Non-Docker indexers are not retried.

---

## Planned Changes

- [x] Create `.gitignore`
- [x] Write `info_opencode_runner.md`
- [x] Implement backend (`backend/main.py`, `backend/requirements.txt`)
- [x] Add prompt construction (`backend/prompts.py`, `backend/W.md`)
- [x] Implement frontend (Vite + React + TypeScript)
- [x] Add Tauri desktop wrapper (`frontend/src-tauri/`)
- [x] Add `start.sh` startup script
- [x] Test end-to-end (TypeScript + Python syntax checks, Vite build passes)
- [x] Fix `variables_prompt()` missing args bug in `main.py`
- [x] Sync docs to final state
- [x] Split doc: extract L2 clustering into sub-document

---

## Log

- 2026-02-10 :: william :: Created doc, planned OpenCode CLI Web Wrapper feature
- 2026-02-10 :: william :: Implemented full stack: backend (FastAPI), frontend (Vite + React + TS), verified builds pass
- 2026-02-16 :: william :: Synced docs to current state: updated purpose to C4 classification, added prompts.py/W.md/Tauri/start.sh to docs, fixed variables_prompt() missing args bug in main.py
- 2026-02-16 :: william :: Split doc: extracted L2 clustering logic into info_opencode_runner_L2_clustering.md, refocused runner doc on web app mechanics
- 2026-02-16 :: william :: Parallel SCIP in Part 1: added _get_scip_cmd() helper, part1_stream() now launches OpenCode + SCIP indexer concurrently via asyncio.Queue drain tasks. part2_stream() skips indexer if .scip already exists. Removed dead event_stream() code. Updated doc with streaming system flow, /api/run/stream endpoint, _get_scip_cmd helper, and parallel SCIP key decision.
- 2026-02-17 :: william :: Documented available models per provider (replaces "Default Models" section). Frontend now shows model dropdown filtered by provider.
- 2026-02-17 :: william :: Migrated all LLM calls from anthropic SDK to litellm. Updated PROVIDER_ENV_MAP (google → GEMINI_API_KEY). Ticket generation endpoint now uses litellm.completion(). Replaced anthropic>=0.25 with litellm in requirements.txt.
- 2026-02-18 :: william :: Doc sync: Marked part3 as implemented. Clarified part1 runs both modules + edges steps. Added "edges" step option. Added cross-references to decision/ticket endpoints documented in other info files.
- 2026-02-18 :: william :: Added backend test suite (backend/tests/). test_api_endpoints.py covers /api/map, decision CRUD, /api/change-records, /api/tickets (15 tests using FastAPI TestClient with temp DB).
- 2026-03-02 :: william :: Added Docker SCIP retry logic: both part1_stream() and part2_stream() retry Docker SCIP invocations up to 3 times (2s delay) when indexer fails due to empty volume mount. Fixed _get_scip_cmd() priority order in docs (Docker first, not local first). Added SCIP_DOCKER_MAX_RETRIES, SCIP_DOCKER_RETRY_DELAY constants and _is_docker_scip_cmd() helper.
