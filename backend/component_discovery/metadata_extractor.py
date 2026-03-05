# Doc: Natural_Language_Code/component_discovery/info_component_discovery.md
"""
Extract lightweight per-file metadata from SCIP + source.

No LLM calls — pure data extraction. Produces file metadata needed
for the LLM cluster analysis prompt (docstrings, exports, symbol kinds, LOC).
"""

import re
from collections import defaultdict
from pathlib import Path, PurePosixPath

from component_discovery.scip_pb2 import Index

# Symbol kind enum values -> readable names
_KIND_NAMES = {
    7: "Class",
    17: "Function",
    26: "Method",
    9: "Constructor",
    11: "Enum",
    21: "Interface",
    53: "Trait",
    8: "Constant",
    15: "Field",
    29: "Module",
}


def _is_local_symbol(symbol: str) -> bool:
    return symbol.startswith("local ")


def _is_definition(symbol_roles: int) -> bool:
    return (symbol_roles & 0x1) != 0


def _extract_symbol_name(symbol: str) -> str:
    """Extract readable name from a SCIP symbol string."""
    parts = symbol.rstrip(".").split("/")
    if not parts:
        return symbol
    last = parts[-1]
    last = re.sub(r'\([^)]*\)\.$', '', last)
    last = last.rstrip("#")
    return last if last else symbol


def _extract_docstring(source_path: Path, max_lines: int = 50) -> str:
    """Extract module-level docstring from first N lines of a Python file."""
    if not source_path.suffix == ".py":
        return ""
    try:
        with open(source_path, "r", encoding="utf-8", errors="replace") as f:
            lines = []
            for i, line in enumerate(f):
                if i >= max_lines:
                    break
                lines.append(line)
        content = "".join(lines)
        for match in re.finditer(r'^(?:"""(.*?)"""|\'\'\'(.*?)\'\'\')', content, re.DOTALL):
            docstring = match.group(1) or match.group(2)
            if docstring:
                docstring = docstring.strip()
                if len(docstring) > 500:
                    docstring = docstring[:500] + "..."
                return docstring
        return ""
    except Exception:
        return ""


def _count_lines(source_path: Path) -> int:
    """Count lines of code."""
    try:
        with open(source_path, "r", encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def extract_file_metadata(
    scip_path: str | list[str],
    source_dir: str,
    module_files: set[str],
) -> dict[str, dict]:
    """
    Extract per-file metadata for files within a module.

    Args:
        scip_path: Path to .scip protobuf file, or list of paths (merged across languages)
        source_dir: Path to source directory root
        module_files: Set of file paths within this module

    Returns:
        dict mapping file_path -> metadata dict with:
            path, directory, docstring, imports, exports,
            symbol_kinds, lines_of_code
    """
    source_root = Path(source_dir) if source_dir else None
    source_available = source_root is not None and source_root.is_dir()

    # Support single path or list of paths
    scip_paths = [scip_path] if isinstance(scip_path, str) else scip_path

    # Collect all SCIP documents across all index files
    all_documents = []
    for sp in scip_paths:
        index = Index()
        with open(sp, "rb") as f:
            index.ParseFromString(f.read())
        all_documents.extend(index.documents)

    # Build raw-path -> normalized-path mapping.
    # SCIP paths may include a repo name prefix (e.g. "Legend/backend/main.py")
    # while module_files uses repo-relative paths ("backend/main.py").
    raw_to_normalized: dict[str, str] = {}
    for doc in all_documents:
        rp = doc.relative_path
        if rp in module_files:
            raw_to_normalized[rp] = rp
        else:
            # Try to find module_file that is a suffix of this raw path
            for mf in module_files:
                if rp.endswith("/" + mf) or rp.endswith(mf):
                    raw_to_normalized[rp] = mf
                    break

    files_metadata = {}

    for doc in all_documents:
        file_path = raw_to_normalized.get(doc.relative_path)
        if file_path is None:
            continue

        source_path = source_root / file_path if source_available else None

        exports = []
        symbol_kinds = defaultdict(list)
        imports = set()

        for sym_info in doc.symbols:
            symbol = sym_info.symbol
            if _is_local_symbol(symbol):
                continue

            kind_value = sym_info.kind
            kind_name = _KIND_NAMES.get(kind_value)

            if kind_name:
                name = _extract_symbol_name(symbol)
                symbol_kinds[kind_name].append(name)

                if kind_name in ("Class", "Function", "Interface", "Trait"):
                    exports.append(name)

        for occ in doc.occurrences:
            symbol = occ.symbol
            if _is_local_symbol(symbol) or _is_definition(occ.symbol_roles):
                continue
            if "/" in symbol and not symbol.startswith("local"):
                parts = symbol.split("`")
                if len(parts) >= 2:
                    module_part = parts[1].rstrip("`")
                    if module_part and module_part not in imports:
                        imports.add(module_part)

        docstring = ""
        lines = 0
        if source_path and source_path.exists():
            docstring = _extract_docstring(source_path)
            lines = _count_lines(source_path)

        files_metadata[file_path] = {
            "path": file_path,
            "directory": str(PurePosixPath(file_path).parent),
            "docstring": docstring,
            "imports": sorted(imports)[:20],
            "exports": exports[:30],
            "symbol_kinds": dict(symbol_kinds),
            "lines_of_code": lines,
        }

    # Add entries for module files not in SCIP (e.g. config files)
    for file_path in module_files:
        if file_path not in files_metadata:
            source_path = source_root / file_path if source_available else None
            docstring = ""
            lines = 0
            if source_path and source_path.exists():
                docstring = _extract_docstring(source_path)
                lines = _count_lines(source_path)

            files_metadata[file_path] = {
                "path": file_path,
                "directory": str(PurePosixPath(file_path).parent),
                "docstring": docstring,
                "imports": [],
                "exports": [],
                "symbol_kinds": {},
                "lines_of_code": lines,
            }

    return files_metadata
