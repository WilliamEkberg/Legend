# SCIP Engine

A polyglot [SCIP](https://github.com/sourcegraph/scip) indexer runner. Point it at any codebase and get raw `.scip` protobuf files ready for downstream processing.

SCIP (Sourcegraph Code Intelligence Protocol) captures precise, cross-reference-aware information about code: definitions, references, symbol relationships, and documentation. This tool handles the hard part — detecting languages, running the right indexers, and producing the `.scip` files.

## Prerequisites

- **Docker** — that's it.

## Quick Start

All commands below assume you're in the **`scip-engine/`** directory (the repository root).

### 1. Build the Docker image (one-time)

```bash
cd legend-indexer
docker build -t scip-engine .
cd ..
```

### 2. Make scripts executable (once, if needed)

```bash
chmod +x scripts/*.sh
```

### 3. Index a codebase

```bash
# Index a GitHub repo (clones it for you)
./scripts/ingest-github.sh https://github.com/owner/repo

# Index a local codebase (pass a filesystem path, NOT a URL)
./scripts/analyze-local.sh /path/to/codebase
```

Output `.scip` files are written to `./output/`.

That's it — the scripts handle cloning, language detection, and indexer execution for you.

## Scripts

> **Run all scripts from the `scip-engine/` root directory** (NOT from `legend-indexer/`).

| Script | Purpose |
|--------|---------|
| `./scripts/ingest-github.sh <URL>` | Clones a GitHub repo, indexes it, outputs `.scip` files to `./output/` |
| `./scripts/analyze-local.sh <PATH>` | Analyzes a local codebase (pass a filesystem path, not a URL) |
| `./scripts/run-all-tests.sh` | Runs the full exhaustive test suite — **only needed if you're making changes to the engine itself** (takes 10-15 min) |

## Supported Languages

| Language | SCIP Indexer | Bundled | Install Command (if running outside Docker) |
|----------|-------------|:-:|-----------------|
| TypeScript | `scip-typescript` | Yes | `npm install -g @sourcegraph/scip-typescript` |
| JavaScript | `scip-typescript` | Yes | `npm install -g @sourcegraph/scip-typescript` |
| Python | `scip-python` | Yes | `pip install scip-python` |
| C# | `scip-dotnet` | Yes | `dotnet tool install -g scip-dotnet` |
| Java | `scip-java` | Yes | `coursier install scip-java` |
| Go | `scip-go` | Yes | `go install github.com/sourcegraph/scip-go/cmd/scip-go@latest` |
| Kotlin | `scip-java` | | `coursier install scip-java` |
| Scala | `scip-java` | | `coursier install scip-java` |
| Rust | `rust-analyzer` | | `cargo install scip-rust` (via rust-analyzer) |
| Ruby | `scip-ruby` | | `gem install scip-ruby` (no arm64-linux binary; TODO) |
| C/C++ | `scip-clang` | | See [scip-clang](https://github.com/nickolay/scip-clang) |
| PHP | `scip-php` | | `composer global require sourcegraph/scip-php` |
| Dart | `scip-dart` | | `dart pub global activate scip_dart` |

All 13 languages are **detected** automatically. Languages without an installed indexer are reported but skipped during indexing. When running via Docker, the 6 bundled languages work out of the box. Kotlin, Scala, and PHP indexers are also installed in the Docker image and work automatically, but are not flagged as bundled in the orchestrator.

## SCIP File Format

The `.scip` files are protobuf-encoded and contain:

- **Documents**: One per source file, with all occurrences (definitions, references) and precise positions.
- **Symbols**: Fully-qualified names with documentation, relationships (implements, extends), and type information.
- **Metadata**: Project root, tool info, and language.

Inspect `.scip` files with the [Sourcegraph CLI](https://github.com/sourcegraph/src-cli):

```bash
brew install sourcegraph/src-cli/src
src code-intel print /path/to/index.scip
```

Or parse them programmatically using the [scip protobuf schema](https://github.com/sourcegraph/scip/blob/main/scip.proto).

---

## Advanced: Running Docker Directly

If you need more control than the scripts provide, you can call Docker yourself.

### Analyze a codebase

```bash
# Auto-detect languages and produce .scip files
docker run --rm \
  -v "/path/to/codebase:/workspace" \
  -v "$(pwd)/output:/output" \
  scip-engine /workspace --output /output

# Index only specific languages
docker run --rm \
  -v "/path/to/codebase:/workspace" \
  -v "$(pwd)/output:/output" \
  scip-engine /workspace -l typescript,python --output /output

# Exclude directories
docker run --rm \
  -v "/path/to/codebase:/workspace" \
  -v "$(pwd)/output:/output" \
  scip-engine /workspace -e "vendor/**,test/**" --output /output
```

### Detect languages (no indexing)

```bash
docker run --rm \
  -v "/path/to/codebase:/workspace" \
  scip-engine detect /workspace
```

### Check available indexers

```bash
docker run --rm scip-engine check-indexers
```

### Verbose output

Add `-v` for debug logging:

```bash
docker run --rm \
  -v "/path/to/codebase:/workspace" \
  scip-engine /workspace -v
```

### CLI Reference

```
scip-engine [OPTIONS] [PATH] [COMMAND]

Arguments:
  [PATH]  Path to the codebase to analyze [default: .]

Options:
  -o, --output <DIR>         Output directory for .scip files
  -l, --languages <LANGS>    Languages to analyze (comma-separated)
  -e, --exclude <PATTERNS>   Glob patterns to exclude (comma-separated)
      --indexers-path <DIR>   Path to bundled indexers directory
  -v, --verbose              Enable verbose output

Commands:
  analyze         Analyze a codebase and produce .scip files
  detect          Detect languages in a codebase
  check-indexers  Check which SCIP indexers are available
```

### Default exclude patterns

These directories are excluded automatically:

```
node_modules/**  .git/**  target/**  dist/**  build/**  __pycache__/**  *.min.js  *.min.css
```

Add more with `-e "pattern1,pattern2"`.

---

## Testing

> **Note:** You only need to run tests if you're making changes to the scip-engine code itself. If you're just using the scripts to index codebases, skip this section.

All testing runs through Docker — no local Rust install needed.

### Run everything (recommended after making changes)

From the `scip-engine/` root:

```bash
./scripts/run-all-tests.sh
```

This single command will:
1. Build both Docker images (production + test)
2. Run all cargo tests (unit, integration, robustness)
3. Run clippy lint
4. Clone real test repos (supabase, ollama, zed) if not already present
5. Run real-repo tests against those repos
6. Run an end-to-end indexing smoke test

Takes ~10-15 minutes on first run, faster on subsequent runs.

### Run just the cargo tests

```bash
cd legend-indexer

# Build the test image (one-time, cached after first build)
docker build -f Dockerfile.test -t scip-engine-test .

# Run all tests
docker run --rm scip-engine-test
```

This runs ~47 tests across 4 suites:

| Suite | File | What it covers |
|-------|------|----------------|
| Unit tests | `src/detect.rs`, `src/orchestrate.rs` | Language detection logic, metadata correctness |
| Integration | `tests/integration_test.rs` | CLI commands (help, version, detect, check-indexers, filters) |
| Robustness | `tests/robustness_test.rs` | Bug regressions, edge cases (symlinks, unicode, nested excludes), determinism, report metrics |
| Real-repo | `tests/real_repo_test.rs` | Validates against real cloned repos (auto-skipped if repos aren't present) |

### Run a specific test

```bash
docker run --rm scip-engine-test cargo test test_language_extensions
docker run --rm scip-engine-test cargo test robustness_test::detection
```

### Run clippy (lint)

```bash
docker run --rm scip-engine-test cargo clippy
```

### Making changes and re-testing

After editing Rust source files, rebuild and test:

```bash
# Rebuild picks up your changes (Docker layer caching keeps it fast)
docker build -f Dockerfile.test -t scip-engine-test .
docker run --rm scip-engine-test
```

### Adding new tests

- Shared helpers live in `tests/common/mod.rs` (`create_file`, `find_lang`, `skip_unless!` macro)
- Use `skip_unless!(PATH)` for tests that depend on external resources
- Bug-fix regressions go in `robustness_test.rs` under the `bug_fixes` module
- Detection edge cases go in `robustness_test.rs` under the `detection` module

## Architecture

```
scip-engine/                    # ← Repository root. Run scripts from here.
├── README.md
├── scripts/                    # ← Convenience wrappers (run from repo root)
│   ├── ingest-github.sh
│   ├── analyze-local.sh
│   └── run-all-tests.sh
├── output/                     # Default output directory for .scip files
└── legend-indexer/             # ← Rust project. Run docker build from here.
    ├── Dockerfile              # Production image (binary + all bundled indexers)
    ├── Dockerfile.test         # Test image (Rust toolchain + cargo test)
    ├── Cargo.toml
    └── src/
        ├── main.rs             # CLI entry point, argument parsing
        ├── lib.rs              # Library root — re-exports config, detect, orchestrate
        ├── config.rs           # Default configuration and exclude patterns
        ├── detect.rs           # Language detection via file extensions + config files
        │                         Uses a data-driven LanguageSpec table for all language metadata
        └── orchestrate.rs      # SCIP indexer execution (bundled path, PATH, npx fallback)
```

### How it works

1. **Language detection** (`detect.rs`): Walks the directory tree, counting files by extension and detecting config files (`tsconfig.json`, `Cargo.toml`, `go.mod`, etc.). Produces a `DetectionReport` with coverage stats and unrecognized extensions.

2. **Indexer orchestration** (`orchestrate.rs`): For each detected language, finds the appropriate SCIP indexer — checking the bundled path first, then `$PATH`, then `npx` fallback for Node.js tools — and runs it against the codebase.

3. **Output**: Each indexer produces a `.scip` file (protobuf format) in `.legend-indexer/` inside the codebase. If `--output` is specified, files are copied there and the temp directory is cleaned up.

### What the Docker image contains

- The `legend-indexer` Rust binary (compiled inside the build stage)
- Bundled SCIP indexers for TypeScript, JavaScript, Python, C#, Java, and Go (Kotlin, Scala, and PHP indexers are also installed but not flagged as bundled)
- All runtime dependencies (Node.js, .NET, Go, Python)

No Rust, Node.js, or other toolchains needed on your machine.
