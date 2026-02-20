# SCIP Design Philosophy

> Related: [SCIP Protobuf Schema Reference](./scip-info.md) · [CLI Tools & Language Bindings](./scip-tools.md) · [Per-Language Indexer Reference](./scip-indexers.md)

---

## Origin

SCIP (Source Code Intelligence Protocol) was created by Sourcegraph as the successor to LSIF (Language Server Index Format). It draws direct inspiration from Scala's **SemanticDB**, which demonstrated that a flat, file-oriented data model with string-based symbol identifiers could capture rich navigational information without the complexity of a graph-based format. Sourcegraph adopted and generalized this approach to work across all programming languages.

---

## The core principle: transmission format, not storage/query format

SCIP is designed as a **transmission format optimized for producers** (indexers). It is explicitly *not* a storage format or a query format. Consumers — whether Sourcegraph's backend, a custom analysis tool, or an AI agent — must build their own indexes and query structures from the SCIP data.

This means:
- SCIP files cannot answer "find all references to symbol X" without a full scan
- There are no precomputed reverse indexes, lookup tables, or sorted structures
- Consumers are expected to load the data and build whatever data structures they need

This asymmetry is intentional: there are many indexers (one per language) but few consumers, so optimizing for producer simplicity yields the most leverage.

---

## Design goals

| Goal | Description |
|------|-------------|
| **Navigation fidelity** | Enable IDE-quality code navigation (go-to-definition, find-references, find-implementations, hover docs) within Sourcegraph and downstream tools |
| **Producer accessibility** | Make it easy to write new indexers — no complex state machines, no integer ID coordination, no graph construction required |
| **Cross-repository support** | Symbol identifiers are globally unique by construction, so cross-repo and cross-language navigation work without coordination between indexers |
| **File-level incrementality** | The flat document array allows re-indexing individual files without rebuilding the full index, important for monorepo performance |
| **Resilience** | Incorrect data for one symbol or file has limited blast radius — no shared integer ID tables that corrupt everything when one entry is wrong |
| **Debuggability** | Human-readable symbol strings mean you can inspect and debug index contents without a separate symbol table or decoder |

---

## Explicit non-goals

| Non-goal | Rationale |
|----------|-----------|
| **Code modification / refactoring** | SCIP is read-only navigation data. Write operations like rename-symbol or extract-method are out of scope |
| **Consumer-side optimization** | The format does not try to be efficient to query directly. Consumers must build their own indexes |
| **Uncompressed compactness** | SCIP relies on standard compression (gzip/zstd) rather than internal compression. The highly repetitive symbol strings compress well — typically to 10–20% of original size |
| **Standalone navigation** | A `.scip` file alone cannot power interactive navigation (e.g. "find all references") without a query engine that builds reverse indexes |

---

## SCIP vs LSIF

LSIF (Language Server Index Format) was SCIP's predecessor. SCIP addressed several fundamental pain points:

| Dimension | LSIF | SCIP |
|-----------|------|------|
| **Serialization** | JSON (newline-delimited) | Protobuf (binary) |
| **Symbol identity** | Integer IDs with forward references | Human-readable string identifiers |
| **Data model** | Graph of vertices and edges | Flat array of documents |
| **File size** | Baseline | **~8× smaller** |
| **Parse speed** | Baseline | **~3× faster** |
| **Cross-repo support** | Requires coordination layer | Built-in (string symbols are globally unique) |
| **Debugging** | Requires resolving integer ID chains | Symbols are directly readable |
| **Streaming** | Edge ordering constraints | Documents can be emitted in any order |
| **Blast radius of bugs** | Corrupted ID breaks all references through it | Corrupted symbol affects only that symbol |

The integer-to-string ID shift is the most consequential change. In LSIF, a misassigned integer ID could silently corrupt navigation for an entire file or package. In SCIP, each symbol string is self-contained — a bug in one symbol's identifier cannot cascade.

---

## Key architectural choices

### String-based symbols
Symbol identifiers encode scheme, package manager, package name, version, and fully-qualified descriptor path into a single human-readable string. This eliminates the need for symbol tables, enables cross-repo matching by string equality, and makes debugging trivial (you can `grep` for a symbol in a text dump).

### Flat document array
Instead of a graph of interconnected nodes, SCIP uses a flat list of `Document` messages, each containing all occurrences and symbol information for one source file. This enables streaming serialization (emit documents as files are processed), trivial parallelism (process documents independently), and file-level incrementality.

### Protobuf encoding
Protobuf provides binary efficiency, automatic code generation for every major language, TLV (tag-length-value) framing that enables streaming without loading the full message, and built-in forward/backward compatibility rules for schema evolution.

### Compact range encoding
Occurrences use `repeated int32` for ranges instead of structured `Position`/`Range` messages. This single optimization reduced payload size by ~50% in Sourcegraph's benchmarks.

---

## Further reading

- [SCIP Protobuf Schema Reference](./scip-info.md) — field-by-field dissection of every message and enum
- [CLI Tools & Language Bindings](./scip-tools.md) — how to inspect and parse `.scip` files
- [Per-Language Indexer Reference](./scip-indexers.md) — what each indexer produces and its quirks
- [Canonical SCIP design document](https://github.com/sourcegraph/scip/blob/main/DESIGN.md)
- [scip.proto schema](https://github.com/sourcegraph/scip/blob/main/scip.proto)
