# Doc: Natural_Language_Code/component_discovery/info_component_discovery.md
"""
Parse a SCIP protobuf index and filter to module scope by directory prefix.

Extracts three edge types from the whole-codebase SCIP index,
keeping only files within the specified module directories.

Single-pass with deferred resolution:
1. Iterate all documents, collect definitions and SymbolInformation.kind
2. Collect non-definition, non-local references into a deferred list
3. Collect inheritance relationships from SymbolInformation
4. After all documents, resolve references to defining files
5. Filter to files within module directories
"""

import re
from collections import defaultdict

from component_discovery.scip_parser import load_scip_index
from component_discovery.scip_pb2 import Index

# SymbolInformation.Kind values that represent callable symbols
_CALLABLE_KINDS = frozenset({
    17,  # Function
    26,  # Method
    9,   # Constructor
    66,  # AbstractMethod
    80,  # StaticMethod
    68,  # ProtocolMethod
    69,  # PureVirtualMethod
    70,  # TraitMethod
    71,  # TypeClassMethod
    67,  # MethodSpecification
    76,  # SingletonMethod
})

_METHOD_SUFFIX_RE = re.compile(r'\([^)]*\)\.$')


def _is_local_symbol(symbol: str) -> bool:
    return symbol.startswith("local ")


def _is_definition(symbol_roles: int) -> bool:
    return (symbol_roles & 0x1) != 0


def _is_import(symbol_roles: int) -> bool:
    return (symbol_roles & 0x2) != 0


def _is_callable_by_kind(kind: int) -> bool:
    return kind in _CALLABLE_KINDS


def _is_callable_by_suffix(symbol: str) -> bool:
    """Fallback: check if symbol string ends with a method descriptor '().'."""
    return bool(_METHOD_SUFFIX_RE.search(symbol))


def _file_in_module(file_path: str, dir_prefixes: list[str]) -> bool:
    """Check if file_path falls under any of the module's directory prefixes."""
    for prefix in dir_prefixes:
        # Normalize: ensure prefix ends with /
        p = prefix.rstrip("/") + "/"
        if file_path.startswith(p) or file_path == prefix.rstrip("/"):
            return True
    return False


def _detect_path_prefix(raw_paths: list[str], module_dirs: list[str]) -> str:
    """Detect a common prefix in SCIP paths that should be stripped.

    SCIP indexers produce paths relative to their working directory, which may
    include the repo directory name (e.g. ``Legend/backend/main.py`` instead of
    ``backend/main.py``) or even ``../`` components (TypeScript workspace).

    Strategy: for each module dir, check if any SCIP path contains it.  If it
    does but doesn't start with it, the characters before the module dir are
    the prefix to strip.
    """
    if not raw_paths or not module_dirs:
        return ""

    # Normalize module dirs — strip trailing /
    normalized_dirs = [d.rstrip("/") + "/" for d in module_dirs]

    # Find the prefix by looking for a module dir inside raw paths
    for nd in normalized_dirs:
        for rp in raw_paths:
            idx = rp.find(nd)
            if idx > 0:
                return rp[:idx]
    return ""


def _compute_add_prefix(index: Index, raw_paths: list[str], module_dirs: list[str],
                         strip_prefix: str) -> str:
    """Compute prefix to prepend when SCIP paths are module-relative.

    Uses metadata.project_root to reconstruct the module prefix when paths
    don't match any module_dir after stripping.
    """
    if not module_dirs or not raw_paths:
        return ""

    sample = raw_paths[:200]
    for rp in sample:
        adjusted = rp[len(strip_prefix):] if strip_prefix and rp.startswith(strip_prefix) else rp
        if _file_in_module(adjusted, module_dirs):
            return ""

    project_root = ""
    if index.metadata and index.metadata.project_root:
        project_root = index.metadata.project_root

    if not project_root:
        return ""

    if project_root.startswith("file://"):
        path = project_root[len("file://"):]
    else:
        path = project_root

    path = path.rstrip("/") + "/"

    for md in module_dirs:
        md_with_slash = "/" + md.rstrip("/") + "/"
        idx = path.find(md_with_slash)
        if idx >= 0:
            return path[idx + 1:]

    return ""


def parse_scip_for_module(scip_path: str | list[str], module_dirs: list[str]) -> dict:
    """
    Parse one or more SCIP index files and extract edges scoped to one module.

    Args:
        scip_path: Path to a .scip protobuf file, or list of paths (one per language).
                   When a list is given, all files are parsed and results merged.
        module_dirs: List of directory prefixes belonging to this module

    Returns:
        dict with:
            - "call_edges": {(file_a, file_b): count} — callable references
            - "import_edges": {(file_a, file_b): count} — import references
            - "inheritance_edges": {(file_a, file_b): count} — is_implementation
            - "files": set of file paths within module
            - "definitions": int count of definitions in module
    """
    if isinstance(scip_path, list):
        results = []
        for p in scip_path:
            r = parse_scip_for_module(p, module_dirs)
            if r["files"]:  # skip files that failed to parse
                results.append(r)
        if not results:
            return _empty_result()
        return _merge_scip_results(results)
    index = load_scip_index(scip_path)
    if index is None:
        return _empty_result()

    raw_paths = [doc.relative_path for doc in index.documents]
    prefix = _detect_path_prefix(raw_paths, module_dirs)

    add_prefix = _compute_add_prefix(index, raw_paths, module_dirs, prefix)

    # Pass 1: collect definitions, symbol kinds, relationships
    symbol_to_file = {}
    symbol_to_kind = {}
    deferred_refs = []         # (referring_file, symbol, symbol_roles)
    implementation_edges = []  # (implementing_file, impl_symbol, target_symbol)
    all_files = set()

    for doc in index.documents:
        file_path = doc.relative_path
        if prefix and file_path.startswith(prefix):
            file_path = file_path[len(prefix):]
        if add_prefix:
            file_path = add_prefix + file_path
        all_files.add(file_path)

        for sym_info in doc.symbols:
            if sym_info.kind != 0:
                symbol_to_kind[sym_info.symbol] = sym_info.kind

            for rel in sym_info.relationships:
                if rel.is_implementation:
                    implementation_edges.append(
                        (file_path, sym_info.symbol, rel.symbol)
                    )

        for occ in doc.occurrences:
            symbol = occ.symbol
            if not symbol or _is_local_symbol(symbol):
                continue

            if _is_definition(occ.symbol_roles):
                symbol_to_file[symbol] = file_path
            else:
                deferred_refs.append((file_path, symbol, occ.symbol_roles))

    # Filter files to module scope
    module_files = {f for f in all_files if _file_in_module(f, module_dirs)}
    module_defs = sum(1 for s, f in symbol_to_file.items() if f in module_files)

    # Pass 2: resolve references into call and import edges (module-scoped)
    call_edges = defaultdict(int)
    import_edges = defaultdict(int)

    for ref_file, symbol, roles in deferred_refs:
        if ref_file not in module_files:
            continue

        def_file = symbol_to_file.get(symbol)
        if def_file is None or def_file == ref_file or def_file not in module_files:
            continue

        edge_key = tuple(sorted([ref_file, def_file]))

        # Any cross-file reference is a dependency (import edge).
        # The SCIP Import role bit is unreliable for JS/TS, so we treat all
        # non-definition cross-file references as imports.
        import_edges[edge_key] += 1

        kind = symbol_to_kind.get(symbol)
        if kind is not None:
            if _is_callable_by_kind(kind):
                call_edges[edge_key] += 1
        else:
            if _is_callable_by_suffix(symbol):
                call_edges[edge_key] += 1

    # Pass 3: resolve inheritance edges (module-scoped)
    inherit_edges = defaultdict(int)
    for impl_file, _impl_symbol, target_symbol in implementation_edges:
        if impl_file not in module_files:
            continue
        target_file = symbol_to_file.get(target_symbol)
        if target_file is None or target_file == impl_file or target_file not in module_files:
            continue
        edge_key = tuple(sorted([impl_file, target_file]))
        inherit_edges[edge_key] += 1

    return {
        "call_edges": dict(call_edges),
        "import_edges": dict(import_edges),
        "inheritance_edges": dict(inherit_edges),
        "files": module_files,
        "definitions": module_defs,
    }


def _empty_result() -> dict:
    """Return an empty parsed result for graceful degradation."""
    return {
        "call_edges": {},
        "import_edges": {},
        "inheritance_edges": {},
        "files": set(),
        "definitions": 0,
    }


def _merge_scip_results(results: list[dict]) -> dict:
    """Merge parsed SCIP results from multiple language-specific index files."""
    merged_call = defaultdict(int)
    merged_import = defaultdict(int)
    merged_inherit = defaultdict(int)
    merged_files: set[str] = set()
    merged_defs = 0

    for r in results:
        for k, v in r["call_edges"].items():
            merged_call[k] += v
        for k, v in r["import_edges"].items():
            merged_import[k] += v
        for k, v in r["inheritance_edges"].items():
            merged_inherit[k] += v
        merged_files.update(r["files"])
        merged_defs += r["definitions"]

    return {
        "call_edges": dict(merged_call),
        "import_edges": dict(merged_import),
        "inheritance_edges": dict(merged_inherit),
        "files": merged_files,
        "definitions": merged_defs,
    }
