# Component Descriptions

> Part of [Map Descriptions](./info_map_descriptions.md)

## Section 1 (HUMAN EDITS ONLY)

### Purpose

Read a component's source code and extract the technical decisions that define what it does and how. Each component gets a list of concrete, falsifiable statements — not a prose summary — that together describe the component's responsibility, boundaries, and implementation choices.

---

### How it Works

#### Workflow

1. **Gather files** — Collect all source files belonging to the component (from the component discovery output). Exclude test files — they describe how the component is tested, not what it is.

2. **LLM extracts decisions** — Send the source code to an LLM with instructions to extract technical decisions across these categories:
   - **API contracts** — What interfaces does this component expose? What does it accept and return?
   - **Patterns & architecture** — What design patterns are used? (Repository pattern, middleware chain, event-driven, etc.)
   - **Libraries & frameworks** — What external dependencies does this component rely on and how?
   - **Boundaries** — What does this component explicitly NOT do? Where does it delegate to other components?
   - **Error handling** — How does this component handle failures? What error strategies does it use?
   - **Data flow** — What data enters, what transformations happen, what leaves?

3. **Format as list items** — Each decision is a single statement, formatted as a bullet point. Decisions should be self-contained — understandable without reading the source code, but verifiable by reading it.

4. **Attach to map** — Store the decision list against the component in the architecture map output.

#### What Makes a Good Component Description

A good description for a component like "auth" might look like:

- Authenticates requests via JWT tokens validated against a shared secret
- Exposes `authenticate(token) -> User | Error` as the sole public interface
- Delegates user lookup to the `users` component — never queries the database directly
- Returns 401 for expired tokens and 403 for insufficient permissions (no retry)
- Uses `jsonwebtoken` library for token parsing and validation
- Caches validated tokens in-memory (LRU, 5-minute TTL) to avoid repeated validation

Each statement is falsifiable, specific, and captures a structural choice.

#### Rules & Edge Cases

- If a component has only 1-2 small files, the LLM may produce fewer decisions — that's correct, small components have fewer choices to document
- The LLM should not invent decisions that aren't in the code — every statement must be traceable to actual source
- Generated descriptions are drafts — the research agent or a human may refine them later

---

## Section 2 (HUMAN + AI)

### Architecture

#### Data Model

**Input (per component):**
```
component_id:   int              — from DB
component_name: str              — from DB
file_paths:     list[str]        — from DB (component_files, is_test=0)
source_code:    dict[str, str]   — from disk, keyed by relative path
```

**LLM output schema:**
```json
{
  "decisions": [
    {
      "category": "api_contracts | patterns | libraries | boundaries | error_handling | data_flow",
      "text": "Falsifiable statement about this component"
    }
  ]
}
```

**Output (written to DB per decision):**
```
decisions row: component_id, category, text, source='pipeline_generated', pipeline_run_id
```

#### System Flow

```
For each component:
    │
    ├─ DB: get_component_files(component_id, exclude_test=True)
    │
    ├─ Disk: _read_component_source(source_dir, file_paths)
    │         → skip binary files, truncate at 1500 lines each
    │
    ├─ Size check: total chars > 150K?
    │   ├─ No  → single LLM call
    │   └─ Yes → _chunk_files(files, token_budget=150_000)
    │            → multiple LLM calls
    │            → _merge_decisions(batches) to deduplicate
    │
    ├─ Validate: check category against enum, drop invalid
    │
    └─ DB: add_decision() for each valid decision
```

---

### Implementation

#### Functions

```python
describe_all_components(conn, source_dir: str, client, run_id: int) -> None
```
Iterates all components across all modules. Calls `describe_component` for each.

```python
describe_component(conn, component: dict, source_dir: str, client, run_id: int) -> None
```
Single component: read files → LLM call(s) → validate → write to DB.

```python
_read_component_source(source_dir: str, file_paths: list[str]) -> dict[str, str]
```
Reads each file from disk. Skips binary files (based on extension or read error). Truncates files longer than 1500 lines. Returns `{relative_path: content}`.

```python
_chunk_files(files: dict[str, str], token_budget: int = 150_000) -> list[dict[str, str]]
```
Splits files into batches where each batch stays under `token_budget` characters. Files are not split across batches — a single file stays in one batch.

```python
_merge_decisions(batches: list[list[dict]]) -> list[dict]
```
Deduplicates decisions across batch results. Two decisions are duplicates if they share the same category and have high text similarity (exact match or near-match after normalization).

#### Prompt Design

The LLM receives:
1. **System prompt** — Role as a technical architect extracting decisions from source code
2. **File contents** — Each file with a path header: `--- file: src/auth/handler.py ---`
3. **Category definitions** — The 6 component-level categories with one-line explanations:
   - `api_contracts` — Interfaces exposed, input/output contracts
   - `patterns` — Design patterns and architectural choices
   - `libraries` — External dependencies and how they're used
   - `boundaries` — What this component does NOT do, delegation points
   - `error_handling` — Failure modes, error strategies, retry/fallback behavior
   - `data_flow` — Data entering, transformations, data leaving
4. **Examples** — 1 good example per category (drawn from Section 1 examples) + 3 negative examples of decisions to skip (trivially obvious facts like "Written in TypeScript" or "Imports React")
5. **Selectivity rule** — "Only include a decision if it represents a choice where a reasonable alternative existed. Skip facts that are trivially obvious from the technology stack."
6. **Categories-optional note** — "Not every category applies to every component. Only use categories where the component makes a notable decision."
7. **Quantity instruction** — "Extract 2-8 decisions. Fewer for small components, more for complex ones."
8. **Output format** — JSON schema as shown in Data Model above

---

### Key Technical Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| LLM input | Actual source code, not summaries or SCIP | Decisions describe behavior and choices; only the code itself reveals those |
| Token budget | 150K chars (~37K tokens) per LLM call | Fits comfortably within context window with room for prompt + response |
| Chunking strategy | Split files into batches, never split a single file | A file is a coherent unit; splitting mid-file loses context |
| File truncation | 1500 lines per file | Prevents one massive file from dominating the token budget; raised from 500 to reduce information loss |
| Test file exclusion | Exclude files where `is_test=1` | Tests describe testing strategy, not the component's architectural decisions |
| Decision count | 2-8 per component | Tighter range reduces low-signal padding; floor of 2 allows very small components |
| Selectivity gate | "Reasonable alternative" test | Only include decisions where a reasonable alternative existed; filters out trivially obvious facts |
| Negative examples | 3 examples of decisions to skip | Explicitly teaches the LLM what NOT to extract (language facts, trivial exports, obvious imports) |
| Categories optional | Not every category required | Prevents the LLM from treating the 6 categories as a checklist to fill |
| Category validation | Drop decisions with invalid category | Strict enum enforcement keeps data consistent and queryable |

---

## Planned Changes

- [x] Implement `describe_all_components` and `describe_component`
- [x] Implement `_read_component_source` with binary skip and truncation
- [x] Implement `_chunk_files` and `_merge_decisions`
- [x] Write component decision extraction prompt in `prompts.py`
- [x] Add category validation against enum

---

## Log

- 2026-02-16 :: william :: Created doc (Section 1 draft)
- 2026-02-16 :: william :: Filled Section 2 (architecture, implementation, decisions)
- 2026-02-16 :: william :: Implemented component_describer.py
- 2026-02-18 :: william :: Doc sync: Fixed file truncation from 500 to 1500 lines in function description and system flow to match code (MAX_FILE_LINES = 1500) and Key Technical Decisions table.
- 2026-02-19 :: william :: Made extraction prompt more selective: added "reasonable alternative" gate, negative examples, categories-optional note, tightened count to 2-8.
