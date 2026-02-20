# SCIP CLI Tools & Language Bindings

> Related: [SCIP Protobuf Schema Reference](./scip-info.md) · [Design Philosophy](./scip-design.md) · [Per-Language Indexer Reference](./scip-indexers.md)

---

## `scip` CLI

The official CLI tool for inspecting, validating, and converting `.scip` files.

### Installation

```bash
# Go install (requires Go 1.19+)
go install github.com/sourcegraph/scip/cmd/scip@latest

# Or download a release binary from:
# https://github.com/sourcegraph/scip/releases
```

### Commands

| Command | Purpose | Example |
|---------|---------|---------|
| `scip print` | Human-readable dump of an index | `scip print index.scip` |
| `scip print --json` | JSON dump for programmatic use | `scip print --json index.scip \| jq .` |
| `scip stats` | Summary statistics (documents, symbols, occurrences) | `scip stats index.scip` |
| `scip lint` | Validate an index for schema conformance | `scip lint index.scip` |
| `scip convert` | Convert to other formats | `scip convert --to=lsif index.scip` |
| `scip snapshot` | Generate snapshot test files from an index | `scip snapshot --from index.scip --to snapshots/` |

`scip print` is the most useful starting point — it renders each document with its occurrences and symbol information in a readable format, letting you verify what an indexer actually produced.

---

## Go bindings (primary)

The Go bindings are the **richest and best-maintained** API for working with SCIP data. They are maintained by Sourcegraph as part of the core `scip` repository.

```bash
go get github.com/sourcegraph/scip/bindings/go/scip
```

### Reading an index from a file

```go
package main

import (
    "fmt"
    "os"

    "google.golang.org/protobuf/proto"
    pb "github.com/sourcegraph/scip/bindings/go/scip/proto"
)

func main() {
    data, _ := os.ReadFile("index.scip")
    var index pb.Index
    proto.Unmarshal(data, &index)

    fmt.Printf("Project root: %s\n", index.Metadata.ProjectRoot)
    fmt.Printf("Documents: %d\n", len(index.Documents))
    for _, doc := range index.Documents {
        fmt.Printf("  %s: %d occurrences, %d symbols\n",
            doc.RelativePath, len(doc.Occurrences), len(doc.Symbols))
    }
}
```

### Streaming parse for large indexes

For indexes that exceed available memory, use `IndexVisitor.ParseStreaming` to process documents incrementally:

```go
import (
    "context"
    "os"

    scip "github.com/sourcegraph/scip/bindings/go/scip"
)

func main() {
    f, _ := os.Open("index.scip")
    defer f.Close()

    visitor := &scip.IndexVisitor{
        VisitMetadata: func(ctx context.Context, m *scip.Metadata) error {
            fmt.Printf("Tool: %s %s\n", m.ToolInfo.Name, m.ToolInfo.Version)
            return nil
        },
        VisitDocument: func(ctx context.Context, d *scip.Document) error {
            fmt.Printf("File: %s (%d occurrences)\n",
                d.RelativePath, len(d.Occurrences))
            return nil
        },
        VisitExternalSymbol: func(ctx context.Context, si *scip.SymbolInformation) error {
            return nil // skip external symbols
        },
    }
    visitor.ParseStreaming(context.Background(), f)
}
```

### Parsing and formatting symbols

```go
import scip "github.com/sourcegraph/scip/bindings/go/scip"

// Parse a symbol string into its components
sym, err := scip.ParseSymbol("scip-go gomod github.com/example/pkg v1.0.0 MyStruct#Method().")
// sym.Scheme    = "scip-go"
// sym.Package   = {Manager: "gomod", Name: "github.com/example/pkg", Version: "v1.0.0"}
// sym.Descriptors = [{Name: "MyStruct", Suffix: Type}, {Name: "Method", Suffix: Method}]

// Format a symbol back to string (with customizable output)
formatted, _ := scip.VerboseSymbolFormatter.Format(symbolString)

// Descriptor-only formatting (strips scheme and package)
short, _ := scip.DescriptorOnlyFormatter.Format(symbolString)
```

### Key utility functions

| Function | Purpose |
|----------|---------|
| `ParseSymbol(s)` | Parse a symbol string into structured `Symbol` |
| `IsGlobalSymbol(s)` | Check if a symbol is global (not `local N`) |
| `IsLocalSymbol(s)` | Check if a symbol is file-local |
| `FindSymbol(doc, name)` | Look up a `SymbolInformation` by name in a document |
| `FindOccurrences(occs, line, char)` | Find occurrences at a given position |
| `CanonicalizeDocument(doc)` | Normalize and merge duplicate entries |
| `SortOccurrences(occs)` | Sort occurrences by range for binary search |
| `NewRange(scipRange)` | Convert `[]int32` range to structured `Range` |
| `SortDocuments(docs)` | Sort documents by relative path |
| `FlattenDocuments(docs)` | Merge documents with the same relative path |

---

## Python bindings (secondary)

Python bindings are **protoc-generated** from `scip.proto`. They are the fastest path to parsing SCIP data for prototyping and analysis scripts.

### Installation

```bash
pip install protobuf

# Then generate Python bindings from scip.proto:
protoc --python_out=. scip.proto

# Or use the pre-generated bindings:
pip install scip-python  # includes scip_pb2.py
```

### Full parsing example

```python
from scip_pb2 import Index

# Read and parse
with open("index.scip", "rb") as f:
    index = Index()
    index.ParseFromString(f.read())

print(f"Project root: {index.metadata.project_root}")
print(f"Documents: {len(index.documents)}")

# Iterate documents and occurrences
for doc in index.documents:
    print(f"\n--- {doc.relative_path} ({doc.language}) ---")
    for occ in doc.occurrences:
        roles = []
        if occ.symbol_roles & 0x1: roles.append("def")
        if occ.symbol_roles & 0x2: roles.append("import")
        if occ.symbol_roles & 0x8: roles.append("read")
        if occ.symbol_roles & 0x4: roles.append("write")

        range_str = f"L{occ.range[0]}:{occ.range[1]}-{occ.range[2]}"
        print(f"  {range_str}  {','.join(roles):10s}  {occ.symbol}")

# Build a definition index
definitions = {}
for doc in index.documents:
    for occ in doc.occurrences:
        if occ.symbol_roles & 0x1:  # Definition flag
            definitions[occ.symbol] = (doc.relative_path, occ.range)

# Look up references
target = "scip-go gomod example.com/pkg v1.0.0 MyFunc()."
if target in definitions:
    path, r = definitions[target]
    print(f"Defined at {path}:{r[0]}:{r[1]}")
```

### Building a cross-reference index

```python
from collections import defaultdict

refs_by_symbol = defaultdict(list)
for doc in index.documents:
    for occ in doc.occurrences:
        if occ.symbol and not occ.symbol.startswith("local "):
            refs_by_symbol[occ.symbol].append({
                "file": doc.relative_path,
                "range": list(occ.range),
                "is_def": bool(occ.symbol_roles & 0x1),
            })

# Now refs_by_symbol["some.symbol.string"] gives all locations
```

---

## Rust bindings

```bash
cargo add scip
```

The Rust crate provides generated protobuf types. Use `prost` to decode:

```rust
use scip::types::Index;
use prost::Message;

let data = std::fs::read("index.scip")?;
let index = Index::decode(&data[..])?;
```

---

## TypeScript bindings

```bash
npm install @sourcegraph/scip-typescript
```

TypeScript bindings are protobuf-generated. Parse with the `protobufjs` runtime.

---

## Canonical schema reference

The authoritative source for all SCIP message and enum definitions:

- **Proto file:** [github.com/sourcegraph/scip/blob/main/scip.proto](https://github.com/sourcegraph/scip/blob/main/scip.proto)
- **Field-by-field reference:** [scip-info.md](./scip-info.md)

---

## Further reading

- [SCIP Protobuf Schema Reference](./scip-info.md) — every message and enum explained
- [Design Philosophy](./scip-design.md) — why SCIP works the way it does
- [Per-Language Indexer Reference](./scip-indexers.md) — what each indexer produces
