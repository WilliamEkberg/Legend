# Everything inside a SCIP index file

> Related: [Design Philosophy & SCIP vs LSIF](./scip-design.md) · [CLI Tools & Language Bindings](./scip-tools.md) · [Per-Language Indexer Reference](./scip-indexers.md)

**A SCIP index is a compact Protobuf binary that encodes every symbol definition, reference, relationship, and diagnostic across an entire codebase — but explicitly omits source text, ASTs, and control flow.** This makes it the densest portable representation of a codebase's navigational intelligence, roughly **8× smaller than LSIF** and 3× faster to process. Designed by Sourcegraph as LSIF's successor and inspired by Scala's SemanticDB, SCIP uses human-readable string-based symbol identifiers instead of opaque numeric IDs, enabling cross-repository and cross-language navigation by design. What follows is a complete, field-by-field dissection of every data structure in the format.

---

## The protobuf schema: 11 messages and 10 enums

The SCIP protocol (v0.6.1, `scip.proto`, proto3 syntax) defines **11 messages** and **10 enums**. The top-level entry point is the `Index` message, which contains exactly three fields:

```
message Index {
  Metadata metadata = 1;                        // required, must appear first in stream
  repeated Document documents = 2;              // one per source file
  repeated SymbolInformation external_symbols = 3;  // optional: symbols from external packages
}
```

The `metadata` field must appear at the start of the Protobuf stream and only once. Documents and external symbols may appear in any order, enabling **streaming serialization** — an indexer can emit documents as it processes files without holding the entire index in memory. The `external_symbols` field allows an index to carry hover documentation for symbols defined in packages outside the indexed project, useful when the external package will not be separately indexed.

The full message inventory is: `Index`, `Metadata`, `ToolInfo`, `Document`, `Symbol`, `Package`, `Descriptor`, `SymbolInformation`, `Relationship`, `Occurrence`, and `Diagnostic`. The ten enums are: `ProtocolVersion`, `TextEncoding`, `PositionEncoding`, `SymbolRole`, `SyntaxKind` (37 values), `Severity`, `DiagnosticTag`, `Language` (**110 language values**), `Descriptor.Suffix` (10 values), and `SymbolInformation.Kind` (83 values covering everything from `Class` to `Axiom` to `SingletonMethod`).

---

## Metadata: tool identity, project root, and encoding

The `Metadata` message captures four pieces of information about the index itself:

| Field | Type | Purpose |
|-------|------|---------|
| `version` | `ProtocolVersion` | Protocol version (currently only `UnspecifiedProtocolVersion = 0`) |
| `tool_info` | `ToolInfo` | Name, version, and CLI arguments of the indexer tool |
| `project_root` | `string` | URI-encoded absolute path to the project root directory |
| `text_document_encoding` | `TextEncoding` | Encoding of source files on disk (`UTF8` or `UTF16`) |

The nested `ToolInfo` message stores the indexer's `name` (e.g., `"scip-typescript"`), `version` (e.g., `"0.2.0"`), and `arguments` (the exact CLI invocation). The `project_root` establishes the base for all relative paths in the index — every `Document.relative_path` must be a subdirectory of this root.

---

## Document-level data: one entry per source file

Each `Document` represents a single source file and carries six fields:

| Field | Type | # | Purpose |
|-------|------|---|---------|
| `relative_path` | `string` | 1 | **Required.** Path relative to project root. Must use `/` separators, no leading slash, no symlinks, canonical form. |
| `occurrences` | `repeated Occurrence` | 2 | Every symbol reference, definition, and syntax token in the file |
| `symbols` | `repeated SymbolInformation` | 3 | Metadata for symbols "defined" within this file |
| `language` | `string` | 4 | Language identifier (the `Language` enum standardizes ~110 names, but this is typed as string to permit any language) |
| `text` | `string` | 5 | **Optional.** Full source text. Indexers typically omit this; clients read from the filesystem instead. |
| `position_encoding` | `PositionEncoding` | 6 | How character offsets are encoded: `UTF8CodeUnitOffsetFromLineStart` for Go/Rust/C++, `UTF16CodeUnitOffsetFromLineStart` for JVM/.NET/JS/TS, `UTF32CodeUnitOffsetFromLineStart` for Python |

The `text` field was introduced specifically to support `SymbolInformation.signature_documentation`, which uses a `Document` message to represent method signatures with optional hyperlinked occurrences. In practice, for a million-line monorepo, the vast majority of index size comes from `occurrences` — the flat list of every token with navigational meaning.

---

## SymbolInformation: the richest data per symbol

`SymbolInformation` is where SCIP stores everything it knows about a defined symbol. It has **eight fields** (field number 2 is intentionally unused):

| Field | Type | # | Purpose |
|-------|------|---|---------|
| `symbol` | `string` | 1 | The unique symbol string identifier (see naming scheme below) |
| `documentation` | `repeated string` | 3 | **Strongly recommended.** Markdown-formatted docs. New indexers should include only non-code documentation (docstrings, comments). |
| `relationships` | `repeated Relationship` | 4 | Links to other symbols: implements, references, type defines, definition overrides |
| `kind` | `Kind` | 5 | Fine-grained symbol kind from an 83-value enum (`Class`, `Method`, `Interface`, `Trait`, `Enum`, `Constructor`, `Property`, `Macro`, `TypeAlias`, etc.) |
| `display_name` | `string` | 6 | Human-friendly name, e.g., `"myMethod"` for the full symbol `com/example/MyClass#myMethod().` |
| `signature_documentation` | `Document` | 7 | A synthetic `Document` encoding the signature (e.g., `void add(int a, int b)` for Java) with optional hyperlinked occurrences |
| `enclosing_symbol` | `string` | 8 | Parent symbol string. Primary use: placing local symbols in a hierarchy for API docs and breadcrumbs. |

The `Kind` enum is remarkably comprehensive — **83 distinct values** covering language-specific constructs like `SingletonClass` (Ruby), `Trait` (Rust/Scala), `TypeClass` (Haskell), `Axiom`/`Lemma`/`Theorem` (proof assistants), `Contract` (Solidity), `Mixin` (Ruby), `PureVirtualMethod` (C++), `Extension` (Swift/Kotlin), and `Concept` (C++20). This replaces the coarser `Descriptor.Suffix` for symbol categorization.

---

## Occurrence data: every token with navigational meaning

Each `Occurrence` ties a source range to a symbol and/or syntax highlighting. It has **seven fields**:

| Field | Type | # | Purpose |
|-------|------|---|---------|
| `range` | `repeated int32` | 1 | Half-open `[start, end)`. Either 3 elements `[line, startChar, endChar]` (single-line) or 4 elements `[startLine, startChar, endLine, endChar]`. **0-based.** |
| `symbol` | `string` | 2 | The symbol string at this position |
| `symbol_roles` | `int32` | 3 | Bitset of `SymbolRole` flags |
| `override_documentation` | `repeated string` | 4 | Overrides `SymbolInformation.documentation` for this specific occurrence (e.g., showing concrete generic types at a call site) |
| `syntax_kind` | `SyntaxKind` | 5 | Syntax highlighting class for this range |
| `diagnostics` | `repeated Diagnostic` | 6 | Compiler errors/warnings at this exact location |
| `enclosing_range` | `repeated int32` | 7 | Range of the nearest non-trivial enclosing AST node. Used for call hierarchies, breadcrumbs, expand-selection, and hover highlight. |

The range encoding is a key optimization: using `repeated int32` instead of structured `Range`/`Position` messages **reduced payload size by ~50%** according to the SCIP team's benchmarks. For definitions, `enclosing_range` spans the entire definition including decorators and docstrings. For references, it spans the parent expression.

---

## SymbolRole and SyntaxKind: the full enums

**SymbolRole** is a bitmask with 7 meaningful flags (values are powers of 2 for bitwise combination):

| Flag | Value | Meaning |
|------|-------|---------|
| `Definition` | 0x1 | Symbol is defined at this occurrence |
| `Import` | 0x2 | Symbol is imported here |
| `WriteAccess` | 0x4 | Symbol is written/assigned here |
| `ReadAccess` | 0x8 | Symbol is read here |
| `Generated` | 0x10 | Occurrence is in generated code |
| `Test` | 0x20 | Occurrence is in test code |
| `ForwardDefinition` | 0x40 | A forward declaration (C/C++ headers, OCaml `.mli` files) |

These flags compose: a symbol can simultaneously be a `Definition | WriteAccess | Test` (value `0x25`), marking a symbol defined in test code with a write.

**SyntaxKind** has **37 values** (plus 2 deprecated aliases) covering syntax highlighting tokens: `Comment`, `Keyword`, `Identifier`, `IdentifierBuiltin`, `IdentifierNull`, `IdentifierConstant`, `IdentifierMutableGlobal`, `IdentifierParameter`, `IdentifierLocal`, `IdentifierShadowed`, `IdentifierNamespace`, `IdentifierFunction`, `IdentifierFunctionDefinition`, `IdentifierMacro`, `IdentifierMacroDefinition`, `IdentifierType`, `IdentifierBuiltinType`, `IdentifierAttribute`, `StringLiteral`, `StringLiteralEscape`, `StringLiteralSpecial`, `StringLiteralKey`, `CharacterLiteral`, `NumericLiteral`, `BooleanLiteral`, `PunctuationDelimiter`, `PunctuationBracket`, `IdentifierOperator`, `RegexEscape`, `RegexRepeated`, `RegexWildcard`, `RegexDelimiter`, `RegexJoin`, `Tag`, `TagAttribute`, and `TagDelimiter`.

---

## Relationships between symbols

The `Relationship` message encodes four boolean relationship types between any two symbols:

| Field | Type | Purpose |
|-------|------|---------|
| `symbol` | `string` | The related symbol's identifier |
| `is_reference` | `bool` | "Find references" on either symbol should include the other. Example: `Dog#sound()` references `Animal#sound()` |
| `is_implementation` | `bool` | "Find implementations" linkage. Typically both `is_implementation` and `is_reference` are true. |
| `is_type_definition` | `bool` | "Go to type definition" linkage |
| `is_definition` | `bool` | Overrides "Go to definition" for symbols without their own definition or with multiple definitions (e.g., inherited fields, mixins) |

These relationships power the three core navigation operations beyond basic go-to-definition: **find references** propagates across `is_reference` links, **find implementations** traverses `is_implementation`, and **go to type definition** follows `is_type_definition`. The `is_definition` flag handles complex cases like single-inheritance fields where a child class "defines" a field inherited from a parent.

---

## The symbol naming scheme: human-readable, globally unique

SCIP's most distinctive design choice is its **string-based symbol format** — a structured, human-readable identifier that uniquely names any code entity across all languages and repositories. The grammar:

```
<symbol>    ::= <scheme> ' ' <package> ' ' <descriptor>+
              | 'local ' <local-id>
<package>   ::= <manager> ' ' <package-name> ' ' <version>
```

A concrete example dissected:

```
scip-typescript npm @sourcegraph/scip-typescript 0.2.0 src/FileIndexer.ts/scriptElementKind().
├─ scheme:    scip-typescript
├─ manager:   npm
├─ package:   @sourcegraph/scip-typescript
├─ version:   0.2.0
└─ descriptors: src/FileIndexer.ts/ (namespace) → scriptElementKind(). (method)
```

**Descriptor suffixes** are single characters that encode the kind of each path component:

| Suffix | Meaning | Example |
|--------|---------|---------|
| `/` | Namespace/package | `java/util/` |
| `#` | Type (class, interface, struct) | `ImmutableList#` |
| `.` | Term (field, constant, value) | `myField.` |
| `().` | Method (with optional disambiguator for overloads) | `toString().` or `of(+1).` |
| `[name]` | Type parameter | `[T]` |
| `(name)` | Parameter | `(node)` |
| `:` | Meta (general-purpose) | `someMeta:` |
| `!` | Macro | `myMacro!` |

**Local symbols** use the format `local <id>` (e.g., `local 42`) and are scoped to a single document — they cannot participate in cross-file navigation. The `'.'` placeholder represents empty values for manager, name, or version fields. Spaces in values are escaped by doubling, and non-ASCII identifiers use backtick escaping: `` `my special name` ``.

Cross-language uniqueness comes from the combination of **scheme** (indexer identity), **package** (manager + name + version), and **descriptors** (fully-qualified path). When two languages share generated code (e.g., Protobuf definitions generating Java and Go bindings), identical symbol strings enable automatic cross-language navigation.

---

## Diagnostic information embedded in the index

Each `Occurrence` can carry `repeated Diagnostic` messages with five fields:

| Field | Type | Purpose |
|-------|------|---------|
| `severity` | `Severity` | `Error` (1), `Warning` (2), `Information` (3), or `Hint` (4) |
| `code` | `string` | Machine-readable diagnostic code (e.g., `"TS2345"`) |
| `message` | `string` | Human-readable description |
| `source` | `string` | Origin tool name (e.g., `"typescript"`, `"super lint"`) |
| `tags` | `repeated DiagnosticTag` | `Unnecessary` (1) or `Deprecated` (2) |

This allows indexers to embed compiler warnings, linter errors, and deprecation notices directly into the navigational index, co-located with the exact source range that triggered them.

---

## Multi-language monorepo handling

SCIP handles multi-language monorepos through **independent per-language indexing with server-side merging**. Each language's indexer (scip-typescript, scip-java, scip-python, etc.) runs independently and produces its own `.scip` file. These are uploaded separately to Sourcegraph, which merges them by matching symbol strings across indexes.

**Currently available indexers** span the major language ecosystems:

- **scip-go** (Go) — GA, full cross-repo support
- **scip-typescript** (TypeScript/JavaScript) — GA, 10× faster than its LSIF predecessor
- **scip-java** (Java, Scala, Kotlin) — GA, uses compiler plugins per language, cross-language JVM navigation
- **scip-python** (Python) — GA, built on Pyright type checker
- **scip-ruby** (Ruby) — GA, full feature support
- **scip-clang** (C/C++) — GA, note: large indexes may hit the 2GB Protobuf message size limit
- **scip-dotnet** (C#, Visual Basic) — GA, built on Roslyn
- **rust-analyzer** (Rust) — SCIP emission built in natively
- **scip-dart** (Dart), **scip-php** (PHP), **scip-protobuf** (Protocol Buffers) — varying maturity
- Community: **scip-zig** (Zig)

Cross-repository navigation works when both the importing and exporting repositories are indexed at compatible versions. When a version mismatch exists or one side lacks an index, Sourcegraph falls back to search-based heuristic navigation. The `external_symbols` field in `Index` allows one index to carry hover docs for external dependencies even when those dependencies are not separately indexed.

---

## What SCIP explicitly does not contain

The DESIGN.md is transparent about SCIP's boundaries. The format is a **transmission format optimized for producers, not a storage or query format for consumers**. Explicitly absent:

- **Full source text** — only file paths and byte-precise ranges, not code content (the optional `Document.text` field exists but indexers typically omit it)
- **Abstract Syntax Trees** — no tree structure is serialized; indexers consume ASTs internally but emit flat occurrence lists
- **Control flow graphs** — no branching, loop, or execution flow information
- **Explicit call graphs** — SCIP records that function B's symbol appears at a location inside function A, but the caller→callee relationship must be inferred by consumers using `enclosing_range`
- **Structured type information** — types appear only as human-readable hover documentation strings, not as queryable data structures or type hierarchies (beyond `is_implementation` relationships)
- **Data flow analysis** — no taint tracking, value flow, or data dependency information
- **Explicit build/package dependency graphs** — dependency relationships are implicit through cross-repo symbol references, not explicitly encoded
- **Runtime behavior** — no profiling, dynamic dispatch resolution, or runtime type information
- **Code modification support** — the design explicitly states that refactoring and write operations are non-goals
- **Efficient navigation by itself** — SCIP requires a query engine (like Sourcegraph's backend) for bidirectional lookups; the format alone cannot answer "find all references" without scanning the entire index

The compression strategy is also external: SCIP uses no built-in compression, relying on gzip/zstd to achieve **10–20% compression ratios** thanks to the highly repetitive nature of symbol strings.

---

## What you can build with SCIP data

SCIP data powers a spectrum of downstream applications, from the directly supported to the derivable:

**Direct navigation features** are the primary use case: go-to-definition, find-all-references, find-implementations, go-to-type-definition, hover documentation, and precise syntax highlighting. These work across files, repositories, and (for shared symbol formats like Protobuf bindings) across languages.

**Dependency and architecture analysis** becomes possible by aggregating cross-file and cross-repo symbol references. You can construct package-level dependency graphs, module coupling metrics, and architecture diagrams showing which components reference which. The `Relationship.is_implementation` edges directly yield interface-implementation maps.

**Dead code detection** is achievable by identifying symbols with `Definition` roles but zero references across all indexed repositories. Impact analysis — "if I change this function, what breaks?" — follows from tracing all references transitively. The `Generated` and `Test` role flags let you separate production code from generated and test code in these analyses.

**AI and LLM applications** are an emerging frontier. Meta integrated SCIP into their **Glean** code intelligence database in roughly 550 lines of code, finding it 8× smaller and 3× faster than LSIF. Sourcegraph uses SCIP to power their **MCP server** for AI coding agents, enriching code context with precise symbol relationships. SCIP-aware chunking improves RAG retrieval quality by providing semantically meaningful code boundaries via `enclosing_range`.

**Practical constraints** to keep in mind: indexing is not incremental at the file level today (modifying one file requires re-running the indexer on the project), indexers are coupled to build systems (tsconfig.json, build.gradle, Cargo.toml), and for large repositories with 10K+ files, indexing can take minutes — making real-time updates impractical. The 2GB Protobuf message size limit can also constrain very large C/C++ indexes.

---

## Conclusion

SCIP achieves a specific and deliberate trade-off: it captures the **complete navigational graph** of a codebase — every definition, reference, import, implementation relationship, and diagnostic — in a format optimized for producer simplicity and transmission efficiency, while deliberately excluding the structural and behavioral information (ASTs, control flow, data flow, call graphs) that would make it a general-purpose program analysis database. Its string-based symbol scheme is the critical innovation: by making symbol identity human-readable and deterministic across languages, it transforms cross-repository and cross-language navigation from an architectural challenge into a string-matching operation. For anyone building on SCIP data, the key insight is that you get a **complete bipartite graph of definitions and references** — and everything else must be derived or obtained elsewhere.