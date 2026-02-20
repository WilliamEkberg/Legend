# Doc: Natural_Language_Code/opencode_runner/info_opencode_runner_L2_clustering.md


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

IDENTITY_PROMPT = """
You are an autonomous coding agent running in unrestricted mode.
"""


# ---------------------------------------------------------------------------
# MODULE_CONTEXT — Step 1: what L2 modules are and how to classify them
# ---------------------------------------------------------------------------

MODULE_CONTEXT = """
## What you are doing

You are identifying the C4 Level 2 modules of a codebase. At this level, a module is any
independently meaningful unit in the architecture — the "big boxes" on the architecture diagram.

## What counts as a module at L2

- **Separately deployable apps**: web applications, APIs, background workers, serverless functions, CLIs
- **Data stores**: databases, caches, message queues, file storage — even managed/external ones
- **Shared libraries**: packages consumed by other modules but not deployed on their own
- **Supporting assets**: CI/CD pipelines, documentation, build tooling, test suites — classify these as `supporting-asset`

## Classification rules

| Has its own deployment config (Dockerfile, vercel.json, serverless.yml, etc.)? | Imported by other modules? | Classification |
|---|---|---|
| Yes | — | `module` |
| No | Yes | `shared-library` |
| No | No | `supporting-asset` |

## What NOT to do in this step

- Do NOT figure out relationships or dependencies between modules — that comes later
- Do NOT read source code files (`.py`, `.ts`, `.js`, `.go`, `.rs`, etc.)
- Do NOT build a dependency graph
- Do NOT add a `relationships` or `consumedBy` field to the output

## What to read

Read only: workspace config files, manifest files (package.json, go.mod, Cargo.toml, pyproject.toml),
Dockerfiles, docker-compose files, and deployment config files (vercel.json, serverless.yml, wrangler.toml,
netlify.toml, Procfile, etc.). These are enough to identify every module.

## Output schema

Write a single JSON file with this exact structure:

```json
{
  "metadata": {
    "generatedAt": "<ISO timestamp>"
  },
  "modules": [
    {
      "id": "short-kebab-case-id",
      "name": "Human Readable Name",
      "classification": "module | shared-library | supporting-asset",
      "type": "web-application | api-service | spa | cli-tool | background-worker | serverless-function | database | cache | message-queue | file-system | api-gateway | ...",
      "technology": "next.js | fastapi | express | postgres | redis | kafka | ...",
      "directories": ["path/to/module/"],
      "sourceOrigin": "in-repo | external",
      "deploymentTarget": "docker | kubernetes | vercel | netlify | aws-lambda | cloudflare-workers | managed-service | local | ..."
    }
  ]
}
```

Fields by classification:
- For `shared-library`: add `"packageName": "@scope/name"` (the npm/pip/cargo package name)
- For `supporting-asset`: add `"category": "ci-cd | testing | build-tooling | documentation | examples | configuration | ..."`
- `deploymentTarget` can be omitted for `supporting-asset`
- `sourceOrigin` is `"external"` for managed services (cloud databases, SaaS queues, etc.) and `"in-repo"` for everything with code in the repo
"""


# ---------------------------------------------------------------------------
# EDGES_CONTEXT — Step 2: what relationships mean and how to identify them
# ---------------------------------------------------------------------------

EDGES_CONTEXT = """
## What you are doing

You already have a JSON file listing all modules. Your job now is to identify the relationships
(edges) between those modules.

## First step — MANDATORY

Read the modules JSON file in the output directory before doing anything else. The file is named
`c4_level2_modules_*.json`. You must know the exact module IDs before you can write edges.

## What to read next

After reading the modules JSON, read manifest files to find dependencies:
- `package.json` files: `dependencies`, `devDependencies` for workspace refs and SDK packages
- `go.mod` / `go.work`: `require` and `replace` directives
- `Cargo.toml`: `[dependencies]` with local `path` entries
- `docker-compose*.yml`: `depends_on`, `links`, and service names
- Any `requirements.txt` or `pyproject.toml` for Python dependencies

Do NOT read source code files.

## Edge types

| Type | When to use |
|---|---|
| `depends_on` | One module imports/uses another as a library or package (manifest-level dependency) |
| `communicates_via` | One module makes runtime API calls to another running service |
| `uses_data_store` | One module connects to a database, cache, queue, or storage module |

For `communicates_via`, include the `protocol` field: `"rest"`, `"graphql"`, `"grpc"`, `"websocket"`, `"async"`.

## consumedBy — for shared libraries

For each `shared-library` module, list which other module IDs consume it.
Look at manifest dependencies: if module A depends on shared-library B, then B is consumed by A.

## Output schema

Write a single JSON file with this exact structure:

```json
{
  "edges": [
    {
      "sourceId": "module-id-from-step1",
      "targetId": "other-module-id-from-step1",
      "type": "depends_on | communicates_via | uses_data_store",
      "protocol": "rest",
      "description": "Short description of the relationship"
    }
  ],
  "consumedBy": [
    {
      "libraryId": "shared-library-id",
      "consumerIds": ["module-id-1", "module-id-2"]
    }
  ]
}
```

Rules:
- Use only IDs that appear in the modules JSON — no new IDs
- Skip `protocol` for `depends_on` and `uses_data_store`
- `consumedBy` can be an empty array if there are no shared libraries
- Do not include self-loops (sourceId == targetId)
- Do not duplicate edges — one edge per (source, target, type) pair
"""


# ---------------------------------------------------------------------------
# Public API — called by main.py
# ---------------------------------------------------------------------------

def modules_system_prompt() -> str:
    """System prompt for Step 1: identify modules, no edges."""
    return IDENTITY_PROMPT + MODULE_CONTEXT


def edges_system_prompt() -> str:
    """System prompt for Step 2: read modules JSON, identify edges."""
    return IDENTITY_PROMPT + EDGES_CONTEXT


MODULES_FILENAME = "c4_level2_modules.json"
EDGES_FILENAME = "c4_level2_edges.json"


def modules_variables_prompt(project_dir: str, output_dir: str) -> str:
    """Task prompt for Step 1 — tells the LLM where to look and what to output."""
    return f"""
## Your task

Analyze the codebase at `{project_dir}` and identify all C4 Level 2 modules.

**Files to read** (read these first — in this order):
1. Root workspace config: `{project_dir}/pnpm-workspace.yaml`, `{project_dir}/package.json`,
   `{project_dir}/go.work`, `{project_dir}/Cargo.toml`, `{project_dir}/pyproject.toml`
   (read whichever exist)
2. All manifest files: glob `{project_dir}/**/package.json`, `{project_dir}/**/go.mod`,
   `{project_dir}/**/Cargo.toml`, `{project_dir}/**/pyproject.toml`
3. All deployment markers: glob `{project_dir}/**/Dockerfile*`,
   `{project_dir}/**/docker-compose*.yml`, `{project_dir}/**/vercel.json`,
   `{project_dir}/**/netlify.toml`, `{project_dir}/**/serverless.yml`,
   `{project_dir}/**/wrangler.toml`, `{project_dir}/**/Procfile`,
   `{project_dir}/**/.platform/`, `{project_dir}/**/railway.json`

**Output**: Write the result to the file `{output_dir}/{MODULES_FILENAME}`.
The output must be valid JSON matching the schema above. Do not create any other files.
"""


def edges_variables_prompt(output_dir: str) -> str:
    """Task prompt for Step 2 — tells the LLM to read modules JSON first, then output edges."""
    return f"""
## Your task

Identify the relationships between the modules that were discovered in the previous step.

**Step 1 — Read modules JSON first (mandatory)**:
Read the file `{output_dir}/{MODULES_FILENAME}` now. You must read it before doing anything else.
Study every module ID carefully — you will reference them in the output.

**Step 2 — Read manifest files**:
Read manifest files (`package.json`, `go.mod`, `Cargo.toml`, `pyproject.toml`, `docker-compose*.yml`,
`requirements.txt`) to find which modules depend on which other modules.

**Output**: Write the result to the file `{output_dir}/{EDGES_FILENAME}`.
The output must be valid JSON matching the schema above. Do not create any other files.
Even if there are no edges, still write the file with `{{"edges": [], "consumedBy": []}}`.
"""


# ---------------------------------------------------------------------------
# Legacy API — used by the non-streaming /api/run endpoint
# ---------------------------------------------------------------------------

def system_prompt():
    """Legacy: combined system prompt for single-pass classification."""
    return modules_system_prompt()


def variables_prompt(project_dir, output_dir, cwd, provider, model, timestamp):
    """Legacy: single-pass variables prompt."""
    return modules_variables_prompt(project_dir, output_dir)
