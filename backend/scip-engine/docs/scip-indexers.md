# SCIP Per-Language Indexer Reference

> Related: [SCIP Protobuf Schema Reference](./scip-info.md) · [Design Philosophy](./scip-design.md) · [CLI Tools & Language Bindings](./scip-tools.md)

This document covers what each language indexer produces, its prerequisites, and known quirks that affect data quality. The data here is synced with the `LanguageSpec` table in `legend-indexer/src/detect.rs`.

---

## Indexer overview

| Language | Indexer | Output stem | Bundled | Key dependency | Install |
|----------|---------|-------------|---------|----------------|---------|
| TypeScript | `scip-typescript` | `typescript` | Yes | `tsconfig.json` | `npm install -g @sourcegraph/scip-typescript` |
| JavaScript | `scip-typescript` | `javascript` | Yes | `package.json` | `npm install -g @sourcegraph/scip-typescript` |
| Python | `scip-python` | `python` | Yes | `pyproject.toml` / `requirements.txt` | `pip install scip-python` |
| C# | `scip-dotnet` | `csharp` | Yes | `.csproj` / `.sln` | `dotnet tool install -g scip-dotnet` |
| Java | `scip-java` | `java` | Yes | `pom.xml` / `build.gradle` | `coursier install scip-java` |
| Kotlin | `scip-java` | `kotlin` | No | `build.gradle.kts` | `coursier install scip-java` |
| Scala | `scip-java` | `scala` | No | `build.sbt` | `coursier install scip-java` |
| Go | `scip-go` | `go` | Yes | `go.mod` | `go install github.com/sourcegraph/scip-go@latest` |
| Rust | `rust-analyzer` | `rust` | No | `Cargo.toml` | `cargo install scip-rust` (via rust-analyzer) |
| Ruby | `scip-ruby` | `ruby` | No | `Gemfile` | `gem install scip-ruby` |
| PHP | `scip-php` | `php` | No | `composer.json` | `composer global require sourcegraph/scip-php` |
| C++ | `scip-clang` | `cpp` | No | `compile_commands.json` | See [scip-clang](https://github.com/nickolay/scip-clang) |
| C | `scip-clang` | `c` | No | `compile_commands.json` | See [scip-clang](https://github.com/nickolay/scip-clang) |
| Dart | `scip-dart` | `dart` | No | `pubspec.yaml` | `dart pub global activate scip_dart` |

**"Bundled"** means the scip-engine includes this indexer in its default Docker image and can run it without additional installation.

**"Output stem"** is the filename stem used for the `.scip` output file (e.g., `index.typescript.scip`). These match the `scip_output_stem` values in `detect.rs`.

---

## Per-language notes

### TypeScript / JavaScript

- **Indexer:** `scip-typescript` handles both TS and JS
- **Requires:** A valid `tsconfig.json` (for TS) or `jsconfig.json`/`package.json` (for JS). Without it, the indexer cannot resolve module paths and will produce incomplete cross-file references
- **Extensions:** `.ts`, `.tsx`, `.mts`, `.cts` (TS) / `.js`, `.jsx`, `.mjs`, `.cjs` (JS)
- **Quality notes:** Excellent definition/reference coverage. JSX/TSX element references are fully tracked. Ambient type declarations (`.d.ts`) are indexed and linked. Path aliases in `tsconfig.json` are resolved
- **Known issue:** Very large monorepos with many `tsconfig.json` files may require explicit project specification

### Python

- **Indexer:** `scip-python`, built on the **Pyright** type checker
- **Requires:** A Python environment with dependencies installed, or type stubs available
- **Extensions:** `.py`, `.pyi`, `.pyw`
- **Quality notes:** Data quality is **directly proportional to type annotation coverage**. Fully annotated code gets precise cross-references; untyped code falls back to heuristic resolution that can miss references or produce false links. Dynamically constructed attributes (`setattr`, `__getattr__`) are generally invisible
- **Tip:** Adding `py.typed` marker files and type stubs for untyped dependencies significantly improves index quality

### Go

- **Indexer:** `scip-go`
- **Requires:** `go.mod` file. The Go toolchain must be able to build the project (`go build ./...` must succeed)
- **Extensions:** `.go`
- **Quality notes:** Excellent. Go's explicit type system and lack of dynamic dispatch make it one of the highest-quality indexers. Cross-module references resolve precisely through `go.mod` dependencies
- **Known issue:** CGo files are partially supported — pure Go code within CGo files is indexed, but C code blocks are skipped

### Java / Kotlin / Scala

- **Indexer:** All three use `scip-java`, which hooks into each language's compiler
- **Requires:** A working build — `pom.xml`/`build.gradle` (Java), `build.gradle.kts` (Kotlin), `build.sbt` (Scala)
- **Extensions:** `.java` / `.kt`, `.kts` / `.scala`, `.sc`
- **Quality notes:** High quality for all three. JVM cross-language references work (e.g., Kotlin calling Java code produces correct links). Annotation processors and generated code are indexed if they are part of the compilation
- **Kotlin-specific:** Kotlin's extension functions and property delegates are fully tracked
- **Scala-specific:** Implicits and type class instances are indexed via the SemanticDB compiler plugin (SCIP's design ancestor)

### C# / Visual Basic

- **Indexer:** `scip-dotnet`, built on the **Roslyn** compiler platform
- **Requires:** `.csproj` or `.sln` file
- **Extensions:** `.cs`, `.csx`
- **Quality notes:** Good coverage through Roslyn's semantic model. NuGet dependency symbols are resolved when packages are restored

### Rust

- **Indexer:** `rust-analyzer` (SCIP emission built in natively — not a separate tool)
- **Requires:** `Cargo.toml`, a working `cargo build`
- **Extensions:** `.rs`
- **Quality notes:** Excellent. Rust's type system provides precise cross-references. Macro-expanded code is indexed (the analyzer expands macros and indexes the result). Trait implementations are linked via `is_implementation` relationships
- **Note:** Invoked via `rust-analyzer scip .` rather than a separate `scip-rust` binary

### Ruby

- **Indexer:** `scip-ruby`, developed by Stripe using **Sorbet**
- **Requires:** `Gemfile`
- **Extensions:** `.rb`, `.rake`, `.gemspec`
- **Quality notes:** Best results on Sorbet-typed codebases. Untyped Ruby code gets basic definition/reference tracking through syntactic analysis but misses dynamically dispatched methods

### C / C++

- **Indexer:** `scip-clang`
- **Requires:** `compile_commands.json` (generated by CMake, Bear, or similar)
- **Extensions:** `.c`, `.h` (C) / `.cpp`, `.cxx`, `.cc`, `.c++`, `.hpp`, `.hxx`, `.hh`, `.h++` (C++)
- **Quality notes:** Good for projects with a correct compilation database. Template instantiations are indexed
- **Known issue:** Very large C++ projects can produce indexes exceeding the **2 GB protobuf message size limit**. Workaround: split indexing by compilation unit or directory

### PHP

- **Indexer:** `scip-php`
- **Requires:** `composer.json`
- **Extensions:** `.php`, `.phtml`, `.php3`, `.php4`, `.php5`, `.phps`
- **Quality notes:** Covers class hierarchies, function calls, and namespace imports. Dynamic features (`__call`, `__get`) are not tracked

### Dart

- **Indexer:** `scip-dart`
- **Requires:** `pubspec.yaml`
- **Extensions:** `.dart`
- **Quality notes:** Covers Dart's type system including generics and mixins. Flutter widget trees are indexed

---

## Data quality factors

These factors affect index quality across all languages. Understanding them helps when interpreting SCIP data or filtering noise.

| Factor | Impact | Mitigation |
|--------|--------|------------|
| **Missing type annotations** | Reduces reference precision in Python, Ruby, JS | Add type annotations or stubs; use `--strict` mode where available |
| **Generated code** | Inflates index with machine-generated symbols | Filter documents by path pattern (e.g., exclude `**/generated/**`, `**/*_pb2.py`) or check the `Generated` flag on occurrences (role bit `0x10`) |
| **Test code** | Test symbols may dominate reference counts | Use the `Test` flag on occurrences (role bit `0x20`) to separate test from production references |
| **Vendored dependencies** | Duplicate symbols from vendored copies | Exclude vendor directories (`vendor/**`, `third_party/**`) during indexing |
| **Dynamic dispatch** | Calls through reflection/dynamic typing are invisible | Accept as a known gap; supplement with runtime profiling data if needed |
| **Macro expansion** | Macro-heavy code may have ranges that don't map cleanly to source | Rust handles this well; C/C++ macro tracking depends on `scip-clang` version |
| **Build configuration** | Wrong or missing build config leads to incomplete indexes | Ensure the project builds successfully before indexing |
| **Monorepo scale** | Very large repos may hit memory or protobuf size limits | Use streaming parse, split indexing by module, or use `scip-clang`'s per-TU mode |

---

## Output file naming

The scip-engine produces output files named `index.<stem>.scip` where `<stem>` matches the `scip_output_stem` from the table above. For a multi-language project, you may see:

```
index.typescript.scip
index.python.scip
index.go.scip
```

These are independent SCIP indexes that can be parsed separately or merged by matching symbol strings across files.

---

## Further reading

- [SCIP Protobuf Schema Reference](./scip-info.md) — every field in the `.scip` format
- [Design Philosophy](./scip-design.md) — why SCIP works the way it does
- [CLI Tools & Language Bindings](./scip-tools.md) — how to inspect and parse these files
