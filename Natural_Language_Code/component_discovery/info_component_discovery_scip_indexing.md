# SCIP Indexing

> Part of [Component Discovery](./info_component_discovery.md)

## Section 1 (HUMAN EDITS ONLY)

### Purpose

Build a `.scip` protobuf file for the entire codebase using language-specific SCIP indexers. One index is built for the whole repository (not per module). The `.scip` file contains every symbol definition, reference, and relationship in the code — this is the raw input that the component discovery pipeline parses and filters per module to build its dependency graph.

---

### How it Works

#### Workflow

1. **Determine language** — Identify the primary language(s) of the repository from file extensions and project files (e.g. `package.json` for TypeScript, `pyproject.toml`/`setup.py` for Python, `go.mod` for Go).

2. **Select indexer** — Choose the appropriate SCIP indexer for each language present:
   - **TypeScript/JavaScript** — `scip-typescript` (requires `tsconfig.json`)
   - **Python** — `scip-python`
   - **Go** — `scip-go`
   - **Java/Kotlin** — `scip-java`
   - **Rust** — `rust-analyzer` with SCIP output
   - **Ruby** — `scip-ruby`
   - Other languages: see [SCIP indexer list](https://sourcegraph.com/docs/code-navigation/references/indexers)

3. **Run indexer** — Execute the indexer against the entire repository root. The indexer performs full semantic analysis (not just text search) — it resolves imports, tracks definitions, and records cross-file references across all files. Output is a single binary protobuf file following the SCIP schema. The component discovery pipeline later filters this index per module using directory prefixes.

4. **Validate output** — Check that the `.scip` file was produced and is non-empty. Parse it to verify it contains documents with occurrences. Report the file count and definition count as a sanity check.

5. **Store for pipeline** — Place the `.scip` file in a known location for the component discovery pipeline to consume.

#### What SCIP Contains

A SCIP index is a protobuf with this structure:
- **Index** — top-level container
  - **Documents** — one per source file, containing:
    - `relative_path` — file path within the project
    - **Occurrences** — each symbol usage (definition or reference) with position, symbol string, and role bitfield
    - **SymbolInformation** — metadata per symbol: kind (Function, Method, Class, etc.) and relationships (is_implementation, is_reference, etc.)

The key data the pipeline extracts:
- Which file defines each symbol (symbol-to-file map)
- Which files reference symbols defined elsewhere (cross-file edges)
- What kind each symbol is (to filter to callable symbols only)
- Inheritance relationships (`Relationship.is_implementation`)

#### Rules & Edge Cases

- Each language indexer has its own prerequisites (e.g. `scip-typescript` needs a valid `tsconfig.json`, `scip-go` needs `go.mod`)
- Multi-language repositories need separate indexer runs; their `.scip` outputs would need to be merged or processed independently
- The SCIP protobuf schema is defined in `scip.proto` — Python bindings are generated via `protoc` into `scip_pb2.py`
- Indexer errors (missing dependencies, syntax errors in source) produce partial outputs — the pipeline handles this gracefully by working with whatever symbols were successfully indexed
- **Docker volume mount failure detection**: On macOS, Docker Desktop's VirtioFS can intermittently fail to populate volume mounts, causing the indexer to see 0 files. The indexer exits with code 1 when `total_files == 0` (instead of silently succeeding), signaling to the backend that a retry is needed. The backend retries Docker SCIP invocations up to 3 times with a 2-second delay between attempts.

---

## Section 2 (HUMAN + AI)

### Architecture

#### TypeScript tsconfig Discovery

**Problem**: `scip-typescript` needs the correct `tsconfig.json` to resolve path aliases (like `@/components/...`). Modern projects use a "project references" pattern:
- Root `tsconfig.json` has `"files": []` and `"references": [{"path": "./tsconfig.app.json"}]`
- The actual `compilerOptions.paths` (with `@/*` aliases) live in `tsconfig.app.json`

If scip-typescript uses the root reference-only tsconfig, it can't resolve alias imports → emits `local N` symbols instead of global references → all cross-file edges are lost.

**Solution — `find_tsconfig_projects()`**: A helper function that discovers the right tsconfig file(s) to pass to scip-typescript:

1. Scan root directory and common subdirectories (`frontend/`, `client/`, `app/`, `src/`, `packages/*`) for `tsconfig*.json` files
2. Exclude non-app configs: `tsconfig.node.json` (Vite node config)
3. Exclude files inside `node_modules`, `.next`, `dist`, `build`, `.legend-indexer`
4. If app-level configs found (e.g. `tsconfig.app.json`) → pass them as positional `[projects...]` args to `scip-typescript index`
5. If only root `tsconfig.json` found → use it as-is (current behavior)
6. If nothing found → use `--infer-tsconfig` fallback

#### Node Dependencies Installation

**Problem**: `scip-typescript` uses the TypeScript compiler under the hood, which needs installed `node_modules` to build a full program and resolve imports. Without installed deps, the compiler can't resolve third-party types or path aliases configured through packages.

**Solution — `ensure_node_deps()`**: Before running the TypeScript indexer, check if dependencies are installed and install them if needed:

1. If `node_modules/` already exists → skip (deps already installed)
2. If `package.json` exists but no `node_modules/`:
   - Detect package manager from lockfile: `yarn.lock` → `npx yarn`, `pnpm-lock.yaml` → `pnpm`/`npx pnpm`, default → `npm`
   - Run install with `--ignore-scripts` (safe/fast, avoids running postinstall hooks in untrusted code)
   - Non-fatal: if install fails, log warning and continue (partial indexing is better than none)

### Implementation

#### Files

| File | Changes |
|------|---------|
| `backend/scip-engine/legend-indexer/src/orchestrate.rs` | tsconfig discovery, npm install, Python npx fallback |
| `backend/scip-engine/legend-indexer/src/detect.rs` | Fixed Python install command (pip → npm) |
| `backend/scip-engine/scripts/install-indexers.sh` | Setup script to install all SCIP indexers locally |
| `backend/scip-engine/legend-indexer/Dockerfile.base` | Pre-built base image with all language runtimes + SCIP indexers |
| `backend/scip-engine/legend-indexer/build-base.sh` | Script to build and tag the base image |
| `backend/scip-engine/legend-indexer/Dockerfile` | Simplified: Rust build + base image (no more from-scratch runtime installs) |

#### Key Functions

- **`find_tsconfig_projects(&self) -> Vec<PathBuf>`** — Walks root + one level of subdirs looking for tsconfig files. Returns paths as positional `[projects...]` args.
- **`ensure_node_deps(&self) -> Result<()>`** — Checks for `node_modules`, detects package manager, runs install if needed. Called before TypeScript indexing.
- **`run_typescript_indexer(&self, output: &Path) -> Result<()>`** — Calls `ensure_node_deps()` first, then uses `find_tsconfig_projects()` to determine args.
- **`run_python_indexer(&self, output: &Path) -> Result<()>`** — Tries bundled → direct binary → npx fallback for scip-python (which is an npm package, not pip).

#### Setup (Local Development)

Install SCIP indexers for local (non-Docker) usage:

```bash
# Install all indexers
./backend/scip-engine/scripts/install-indexers.sh

# Install specific ones
./backend/scip-engine/scripts/install-indexers.sh typescript python
```

Key indexers:
- **TypeScript/JS**: `npm install -g @sourcegraph/scip-typescript`
- **Python**: `npm install -g @sourcegraph/scip-python` (npm, NOT pip)
- **Go**: `go install github.com/sourcegraph/scip-go@latest`

#### Docker Build (Two-Stage Base Image)

The Docker build uses a **pre-built base image** (`legend-indexer-base`) with all language runtimes and SCIP indexers pre-installed. This avoids re-downloading Node.js, Java, .NET, Go, PHP, Maven, Gradle, sbt, etc. on every build.

```bash
# One-time: build the base image (slow, ~10 min — installs all runtimes)
cd backend/scip-engine/legend-indexer
./build-base.sh

# Fast rebuild: only compiles Rust code (~1-2 min with cached deps)
docker build -t scip-engine .
```

**Files:**
- `Dockerfile.base` — Base image with all language runtimes + SCIP indexers
- `build-base.sh` — Script to build and tag the base image
- `Dockerfile` — App image: Rust build stage + copies binary into base image

Rebuild the base image only when adding/updating a language runtime or SCIP indexer version.

### Key Technical Decisions

| Decision | Rationale |
|----------|-----------|
| Prefer `tsconfig.app.json` over root `tsconfig.json` | Root config in Vite/CRA projects is often just a reference container with no paths/compilerOptions |
| Exclude `tsconfig.node.json` | This is Vite's node-side config (for `vite.config.ts`), not app code |
| Use `--ignore-scripts` for npm install | Safety: avoid running arbitrary postinstall scripts in user codebases |
| Non-fatal npm install | Partial indexing (some cross-file edges) is better than failing entirely |
| Scan only root + one level of subdirs | Avoids deep recursive walks; monorepo packages are at predictable depths |
| scip-python via npm, not pip | scip-python is `@sourcegraph/scip-python` npm package (built on Pyright). The Rust orchestrator tries bundled → binary → npx fallback |
| tsconfig paths are positional args | `scip-typescript index [options] [projects...]` — projects go after flags, no `--project` prefix |
| Pre-built base image for Docker | All language runtimes + SCIP indexers in `Dockerfile.base`, built once. Main Dockerfile only compiles Rust (~seconds vs ~10 min). Rebuild base only when updating runtimes. |
| Exit code 1 on empty mount | When `total_files == 0`, the indexer exits with code 1 instead of silently succeeding. This catches Docker VirtioFS mount failures on macOS and triggers the backend's retry logic. |

---

## Planned Changes

*None — current work implemented.*

---

## Log

- 2026-02-16 :: william :: Created doc (Section 1 draft)
- 2026-02-16 :: william :: NLC audit: Fixed scope — whole-codebase indexing (not per-module). Updated purpose, workflow steps 1/2/3, and edge cases to reflect single index filtered per module
- 2026-02-17 :: william :: Added Section 2: tsconfig discovery logic, npm install requirement, key technical decisions. Fix for scip-typescript producing local symbols when path aliases aren't resolved
- 2026-02-17 :: william :: Fixed scip-python install command (was pip, actually npm). Added run_python_indexer with npx fallback. Added install-indexers.sh setup script. Fixed tsconfig args (positional, not --project flag)
- 2026-02-19 :: william :: Split Docker into base image + app image. Created Dockerfile.base with all runtimes pre-installed, build-base.sh to build it, simplified main Dockerfile to only compile Rust and copy binary into base. Eliminates ~10 min of downloads on every rebuild.
- 2026-03-02 :: william :: Added Docker mount failure detection: indexer now exits with code 1 when `total_files == 0` (empty volume mount). Fixes intermittent macOS Docker Desktop VirtioFS race condition where mounts are empty at container start.
