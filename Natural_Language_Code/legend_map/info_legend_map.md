# Legend Architecture Map

## Section 1 (HUMAN EDITS ONLY)

### Purpose

Legend produces a complete architecture map of a codebase by progressively discovering structure at each C4 level: modules (L2), components (L3), and descriptions enriched with technical decisions. The map is the single source of truth for what the system is and why it is built that way.

---

### How it Works

#### Workflow

1. **Module Identification (Part 1 — opencode_runner)** — An AI agent analyzes the codebase and classifies it into C4 Level 2 entities: modules (deployable units), shared libraries, and supporting assets. All are stored in the database as module rows with a `classification` field. Part 1 also writes L2 edges from discovered relationships. Output is ingested into the database; a human reviews the results in the UI before proceeding.

2. **Component Discovery (Part 2 — component_discovery)** — For each module with source code, parse the whole-codebase SCIP index (filtered per module), build a dependency graph, and run Leiden community detection to discover C4 Level 3 components. Each component is a cohesive group of files that work together behind a well-defined interface. Includes shared libraries that have source code. Writes components and L3 edges to the database.

3. **Description Generation (Part 3 — map_descriptions)** — For each component and module, generate human-readable descriptions structured as technical decisions — concrete, falsifiable statements about what the code does and why. Components first (bottom-up from source), then modules (aggregated from component decisions). Writes decisions to the database. After description generation completes, an initial baseline is created to mark the starting point for change tracking.

4. **Research Agent (ongoing — research_agent)** — The system for keeping the map accurate as it evolves. V1: humans edit the map directly (add/modify/remove decisions and components); every edit is tracked as a change record. The review UI highlights accumulated changes since the last baseline. V2 (future): an optional AI feature suggests new technical decisions for human review.

5. **Ticket Generation (on demand — ticket_generation)** — Collects accumulated change records since the last baseline, classifies them as map corrections vs code changes, and generates implementation tickets for code changes. Creates a new baseline after generation. Independent of the research agent — driven by change records from any map edit.

#### Data Flow

```
Codebase
    |
    v
[Part 1] opencode_runner --- AI classifies modules/libraries/assets, writes L2 edges
    |
    v  (writes to DB)
┌─────────────────────────────────┐
│       SQLite Database            │
│  (central store for all steps)   │
└─────────────────────────────────┘
    |                          ^
    v  (reads modules from DB) |
[Part 2] component_discovery   |  (writes components + L3 edges to DB)
    |                          |
    v  (reads from DB)         |
[Part 3] map_descriptions ─────┘  (writes decisions to DB)
    |
    v
    ┌── Human reviews map in UI ──┐
    |                              |
[Research Agent]            [Ticket Generation]
  Map editing +               Change records →
  change tracking               tickets + baseline
```

#### Two Manual Steps

The pipeline has two manual review points:
1. **After Part 1**: Human reviews module classification in UI, then triggers Part 2 + Part 3
2. **After Part 3**: Human edits the map via the research agent, then generates tickets when ready

#### Rules & Edge Cases

- Parts 1-3 run sequentially — each reads from the DB what the previous part wrote
- A module with no source files (e.g. config-only) skips Part 2 and gets a description directly
- The research agent can run at any time after the initial map exists — it does not require a fresh full pipeline run
- Ticket generation is independent of the research agent — it reads change records from any source
- **Re-running Part 1 is a full rebuild** — all modules, components, decisions, and edges are deleted and recreated. A new baseline is created before deletion to fence off existing change records. Human edits are not preserved. The frontend shows a confirmation warning before triggering a Part 1 re-run
- **Re-running Part 3 is safe** — only pipeline-generated decisions are replaced; human-edited decisions (where `source='human'`) are preserved

---

## Sub-documents

- [Module Identification (Part 1)](../opencode_runner/info_opencode_runner.md)
- [Component Discovery (Part 2)](../component_discovery/info_component_discovery.md)
- [Description Generation (Part 3)](../map_descriptions/info_map_descriptions.md)
- [Research Agent](../research_agent/info_research_agent.md)
- [Ticket Generation](../ticket_generation/info_ticket_generation.md)
- [Database](../database/info_database.md)
- [Frontend](../Frontend/info_frontend.md)

---

## Section 2 (HUMAN + AI)

*To be filled in during implementation.*

---

## Planned Changes

- [ ] Implement end-to-end pipeline orchestration

---

## Log

- 2026-02-16 :: william :: Created doc (Section 1 draft)
- 2026-02-16 :: william :: Updated Part 4 description to reflect research agent rewrite (map editing + change tracking model)
- 2026-02-16 :: william :: Added Database and Frontend to sub-documents list
- 2026-02-16 :: william :: NLC audit: Updated data flow to show DB as central store, added 2-step pipeline description, separated ticket generation from research agent, added Ticket Generation sub-document link, updated Part descriptions for V1/V2 scope
- 2026-02-16 :: william :: NLC audit fix: Added baseline creation after Part 3, documented re-run semantics (Part 1 = full rebuild, Part 3 = safe)
