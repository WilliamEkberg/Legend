# Module Descriptions

> Part of [Map Descriptions](./info_map_descriptions.md)

## Section 1 (HUMAN EDITS ONLY)

### Purpose

Aggregate component-level technical decisions into module-level descriptions. A module description captures the cross-cutting patterns, deployment concerns, and elevated decisions that define the module as a whole — not just a concatenation of its components.

---

### How it Works

#### Workflow

1. **Collect component decisions** — Gather the technical decision lists from all components within the module.

2. **Identify cross-cutting patterns** — Find decisions that appear across multiple components in similar form. For example, if 3 out of 5 components use "validates input with Zod schemas," that's a module-level pattern, not a component-level one.

3. **Deduplicate (elevate shared decisions)** — Move cross-cutting decisions up to the module level. Remove them from individual component descriptions to avoid redundancy. The rule: if a decision applies to the majority of components, it belongs at the module level.

4. **Add deployment-level concerns** — Module descriptions include concerns that don't belong to any single component:
   - **Runtime environment** — What platform does this module run on? (Node.js, Python, browser, etc.)
   - **Entry points** — How is the module started or invoked?
   - **Configuration** — How is the module configured? (env vars, config files, CLI args)
   - **Inter-module communication** — How does this module talk to other modules? (HTTP, message queue, shared database, etc.)
   - **Build & deployment** — How is the module built and deployed?

5. **Generate module summary** — Produce the final module description: elevated cross-cutting decisions + deployment concerns + a one-line purpose statement.

#### Rules & Edge Cases

- A module with a single component still gets a module-level description — it adds deployment context that the component description doesn't cover
- Conflicting decisions across components are NOT elevated — they stay at the component level to highlight the inconsistency
- The module description should be useful to someone who has never read the component descriptions — it stands alone as a high-level view

---

## Section 2 (HUMAN + AI)

### Architecture

#### Data Model

**Input (per module):**
```
module:               dict         — id, name, type, technology, deployment_target (from DB)
component_decisions:  dict         — {component_name: [{id, category, text}, ...]} (from DB)
deployment_files:     dict[str,str] — {relative_path: content} (from disk)
```

**LLM output — Elevation call:**
```json
{
  "elevated": [
    {
      "text": "Cross-cutting decision statement",
      "source_decision_ids": [12, 34, 56]
    }
  ]
}
```

**LLM output — Deployment call:**
```json
{
  "decisions": [
    {
      "category": "deployment",
      "text": "Deployment-level decision statement"
    }
  ]
}
```

**Output (written to DB):**
```
Elevated:    decisions row with module_id, category='cross_cutting', text, source='pipeline_generated'
             + DELETE original component decisions by ID
Deployment:  decisions row with module_id, category='deployment', text, source='pipeline_generated'
```

#### System Flow

```
For each module:
    │
    ├─ DB: get_components(module_id)
    ├─ DB: _collect_component_decisions(conn, module_id)
    │       → {component_name: [{id, category, text}, ...]}
    │
    ├─ Component count == 1?
    │   ├─ Yes → skip elevation (no cross-cutting possible)
    │   └─ No  → LLM: elevation call
    │            → For each elevated decision:
    │                DB: add_decision(module_id, 'cross_cutting', text)
    │                DB: delete_decisions_by_ids(source_decision_ids)
    │
    ├─ Disk: _find_deployment_files(source_dir, module_dirs)
    │
    ├─ Deployment files found?
    │   ├─ Yes → LLM: deployment call (files + module metadata)
    │   │        → DB: add_decision(module_id, 'deployment', text) for each
    │   └─ No  → skip deployment call
    │
    └─ Done
```

---

### Implementation

#### Functions

```python
describe_all_modules(conn, source_dir: str, client, run_id: int) -> None
```
Iterates all modules. Calls `describe_module` for each.

```python
describe_module(conn, module: dict, source_dir: str, client, run_id: int) -> None
```
Single module: collect decisions → elevation → deployment → write to DB.

```python
_collect_component_decisions(conn, module_id: int) -> dict[str, list[dict]]
```
Queries all component decisions for a module, grouped by component name. Each decision includes its `id` (needed for elevation deletion).

```python
_find_deployment_files(source_dir: str, module_dirs: list[str]) -> dict[str, str]
```
Scans module directories for deployment-related files. Returns `{relative_path: content}`.

**Deployment file patterns:**

| Pattern | What it reveals |
|---------|-----------------|
| `Dockerfile`, `docker-compose.yml` | Container configuration, base image, ports |
| `package.json` | Node.js dependencies, scripts, engine requirements |
| `requirements.txt`, `pyproject.toml` | Python dependencies |
| `Makefile` | Build commands, targets |
| `.env.example`, `config/*.yml` | Configuration shape and defaults |
| `Procfile`, `vercel.json`, `fly.toml` | Platform-specific deployment config |
| `k8s/`, `helm/` | Kubernetes manifests |

For `package.json`: parse selectively — extract only `scripts` and `dependencies`/`devDependencies`, not the entire file.

#### Orphan Risk on Re-run

If the research agent has been used before a Part 3 re-run, `delete_decisions_by_ids()` during elevation may orphan change records. This happens because the original decision ID (referenced by `change_records.entity_id`) no longer exists after deletion. Ticket generation must handle `entity_id` lookups that return `None` gracefully (skip the record).

#### Elevation Rules

1. **Threshold** — A decision is a candidate for elevation if it appears in similar form across >50% of the module's components
2. **LLM judges similarity** — The LLM receives all component decisions with IDs and identifies semantically similar decisions that represent the same cross-cutting pattern
3. **Returns exact IDs** — The LLM output includes `source_decision_ids` for each elevated decision, enabling precise deletion
4. **Conflicting decisions NOT elevated** — If components disagree (e.g., one uses Zod, another uses Joi), keep both at component level to surface the inconsistency
5. **Single-component modules skip elevation** — No cross-cutting patterns possible with one component; go straight to deployment

#### Prompt Design

**Elevation prompt receives:**
1. Module name, type, technology
2. Component decisions grouped by component, each with its `id`
3. Instructions: find patterns shared across >50% of components, return the merged statement + source IDs
4. Explicit rule: do not elevate conflicting decisions

**Deployment prompt receives:**
1. Module name, type, technology, deployment_target
2. Deployment file contents with path headers
3. Instructions: extract deployment-level decisions (runtime, entry points, configuration, build, inter-module communication)
4. Output as JSON with `category: 'deployment'`

---

### Key Technical Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Two LLM calls per module | Separate elevation and deployment calls | Different tasks with different inputs; combining would confuse the LLM |
| Elevation deletes originals | Component decisions removed by ID from DB | Avoids redundancy; the module-level decision replaces them |
| Elevated category | `cross_cutting` (not the original category) | Re-categorized to distinguish module-level patterns from component-level ones |
| Decision IDs in prompt | LLM sees and returns integer IDs | Enables reliable DB deletion without fuzzy text matching |
| Skip single-component | No elevation call for modules with 1 component | Cross-cutting requires ≥2 components; skip saves an LLM call |
| Selective package.json parsing | Only `scripts` + `dependencies` sections | Full file is noisy (version, license, etc.); scripts and deps are what matter for deployment decisions |
| Module metadata in deployment prompt | Include `type`, `technology`, `deployment_target` from DB | Gives the LLM context to produce more relevant deployment decisions |

---

## Planned Changes

- [x] Implement `describe_all_modules` and `describe_module`
- [x] Implement `_collect_component_decisions` with ID inclusion
- [x] Implement `_find_deployment_files` with pattern table
- [x] Write elevation prompt in `prompts.py`
- [x] Write deployment prompt in `prompts.py`
- [x] Implement selective `package.json` parsing

---

## Log

- 2026-02-16 :: william :: Created doc (Section 1 draft)
- 2026-02-16 :: william :: Filled Section 2 (architecture, implementation, decisions)
- 2026-02-16 :: william :: NLC audit fix: Added orphan risk note for delete_decisions_by_ids during elevation when research agent was used before re-run
- 2026-02-16 :: william :: Implemented module_describer.py
