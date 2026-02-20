"""
Diagnostic script: inspect a SCIP index to find where cross-file edges are being lost.

Checks:
1. Definitions found via occurrences (symbol_roles & 0x1) vs via doc.symbols
2. How many references can be resolved to a defining file vs orphaned
3. Sample unresolved references so we can see what's being missed

Usage:
    python debug_scip.py <path-to-.scip-file> [optional-dir-prefix-filter]
"""

import sys
from collections import defaultdict
from component_discovery.scip_pb2 import Index


def _is_local(symbol: str) -> bool:
    return symbol.startswith("local ")


def analyze(scip_path: str, dir_filter: str | None = None):
    index = Index()
    with open(scip_path, "rb") as f:
        index.ParseFromString(f.read())

    print(f"=== SCIP Diagnostic: {scip_path} ===")
    print(f"Metadata: tool={index.metadata.tool_info.name} v{index.metadata.tool_info.version}")
    print(f"          project_root={index.metadata.project_root}")
    print(f"          documents={len(index.documents)}")
    print(f"          external_symbols={len(index.external_symbols)}")
    print()

    # --- Pass 1: collect definitions from BOTH sources ---
    defs_from_occurrences = {}   # symbol -> file (from occurrence with Definition bit)
    defs_from_doc_symbols = {}   # symbol -> file (from doc.symbols list)
    symbol_to_kind = {}
    all_refs = []                # (ref_file, symbol, roles)
    all_files = set()
    total_occurrences = 0
    total_local_skipped = 0
    total_empty_skipped = 0

    # Per-file stats
    file_occ_counts = {}
    file_sym_counts = {}

    for doc in index.documents:
        fp = doc.relative_path
        all_files.add(fp)

        # doc.symbols: authoritative list of symbols defined in this file
        file_sym_counts[fp] = len(doc.symbols)
        for sym_info in doc.symbols:
            if not _is_local(sym_info.symbol):
                defs_from_doc_symbols[sym_info.symbol] = fp
                if sym_info.kind != 0:
                    symbol_to_kind[sym_info.symbol] = sym_info.kind

        # doc.occurrences: each symbol usage with position and roles
        file_occ_counts[fp] = len(doc.occurrences)
        for occ in doc.occurrences:
            total_occurrences += 1
            if not occ.symbol:
                total_empty_skipped += 1
                continue
            if _is_local(occ.symbol):
                total_local_skipped += 1
                continue

            if (occ.symbol_roles & 0x1) != 0:  # Definition bit
                defs_from_occurrences[occ.symbol] = fp
            else:
                all_refs.append((fp, occ.symbol, occ.symbol_roles))

    # Apply dir filter if given
    if dir_filter:
        prefix = dir_filter.rstrip("/") + "/"
        filtered_files = {f for f in all_files if f.startswith(prefix) or f == dir_filter.rstrip("/")}
        print(f"Dir filter: '{dir_filter}' -> {len(filtered_files)}/{len(all_files)} files match")
    else:
        filtered_files = all_files

    # --- Analysis ---
    print(f"\n--- Definition Sources ---")
    print(f"Definitions from occurrences (symbol_roles & 0x1): {len(defs_from_occurrences)}")
    print(f"Definitions from doc.symbols:                      {len(defs_from_doc_symbols)}")

    # Symbols in doc.symbols but NOT in occurrence-based definitions
    only_in_doc_symbols = set(defs_from_doc_symbols.keys()) - set(defs_from_occurrences.keys())
    only_in_occurrences = set(defs_from_occurrences.keys()) - set(defs_from_doc_symbols.keys())
    in_both = set(defs_from_occurrences.keys()) & set(defs_from_doc_symbols.keys())

    print(f"  In both:                {len(in_both)}")
    print(f"  Only in doc.symbols:    {len(only_in_doc_symbols)}  <-- MISSED by current parser!")
    print(f"  Only in occurrences:    {len(only_in_occurrences)}")

    if only_in_doc_symbols:
        # Check how many references point to these missed symbols
        missed_symbol_set = only_in_doc_symbols
        refs_to_missed = [(f, s, r) for f, s, r in all_refs if s in missed_symbol_set]
        cross_file_refs_to_missed = [
            (f, s, r) for f, s, r in refs_to_missed
            if defs_from_doc_symbols.get(s) and defs_from_doc_symbols[s] != f
        ]
        print(f"\n  References to missed symbols: {len(refs_to_missed)} total, "
              f"{len(cross_file_refs_to_missed)} cross-file")

        # Show samples
        print(f"\n  Sample symbols only in doc.symbols (first 15):")
        for i, sym in enumerate(sorted(only_in_doc_symbols)[:15]):
            kind = symbol_to_kind.get(sym, "?")
            def_file = defs_from_doc_symbols[sym]
            # count refs to this symbol
            ref_count = sum(1 for _, s, _ in all_refs if s == sym)
            cross_ref_count = sum(1 for f, s, _ in all_refs if s == sym and f != def_file)
            print(f"    [{i+1}] kind={kind:>3}  refs={ref_count:>3}  cross_file_refs={cross_ref_count:>3}  "
                  f"file={def_file}")
            print(f"         symbol={sym[:120]}")

    # --- Reference resolution ---
    print(f"\n--- Reference Resolution ---")
    print(f"Total occurrences: {total_occurrences}")
    print(f"  Empty symbol (skipped): {total_empty_skipped}")
    print(f"  Local symbol (skipped): {total_local_skipped}")
    print(f"  Non-local references: {len(all_refs)}")

    # Using occurrence-based defs only (current behavior)
    resolved_occ = 0
    unresolved_occ = 0
    cross_file_edges_occ = defaultdict(int)
    unresolved_symbols_occ = defaultdict(int)

    for ref_file, sym, roles in all_refs:
        def_file = defs_from_occurrences.get(sym)
        if def_file is None:
            unresolved_occ += 1
            unresolved_symbols_occ[sym] += 1
        else:
            resolved_occ += 1
            if def_file != ref_file:
                key = tuple(sorted([ref_file, def_file]))
                cross_file_edges_occ[key] += 1

    # Using combined defs (occurrences + doc.symbols)
    combined_defs = dict(defs_from_doc_symbols)
    combined_defs.update(defs_from_occurrences)  # occurrence-based takes precedence

    resolved_combined = 0
    unresolved_combined = 0
    cross_file_edges_combined = defaultdict(int)

    for ref_file, sym, roles in all_refs:
        def_file = combined_defs.get(sym)
        if def_file is None:
            unresolved_combined += 1
        else:
            resolved_combined += 1
            if def_file != ref_file:
                key = tuple(sorted([ref_file, def_file]))
                cross_file_edges_combined[key] += 1

    print(f"\n  Current parser (occurrence-based defs only):")
    print(f"    Resolved: {resolved_occ}  Unresolved: {unresolved_occ}")
    print(f"    Cross-file edges (unique file pairs): {len(cross_file_edges_occ)}")

    print(f"\n  With doc.symbols defs added:")
    print(f"    Resolved: {resolved_combined}  Unresolved: {unresolved_combined}")
    print(f"    Cross-file edges (unique file pairs): {len(cross_file_edges_combined)}")

    gained_edges = set(cross_file_edges_combined.keys()) - set(cross_file_edges_occ.keys())
    if gained_edges:
        print(f"\n    NEW edges gained by adding doc.symbols: {len(gained_edges)}")
        for i, edge in enumerate(sorted(gained_edges)[:20]):
            w = cross_file_edges_combined[edge]
            print(f"      [{i+1}] {edge[0]}  <->  {edge[1]}  (weight={w})")

    # Apply dir filter to edges
    if dir_filter:
        prefix = dir_filter.rstrip("/") + "/"

        def in_module(f):
            return f.startswith(prefix)

        module_edges_occ = {k: v for k, v in cross_file_edges_occ.items()
                           if in_module(k[0]) and in_module(k[1])}
        module_edges_combined = {k: v for k, v in cross_file_edges_combined.items()
                                if in_module(k[0]) and in_module(k[1])}

        print(f"\n  Module-scoped edges ({dir_filter}):")
        print(f"    Current: {len(module_edges_occ)} edges")
        print(f"    With doc.symbols: {len(module_edges_combined)} edges")
        gained_module = set(module_edges_combined.keys()) - set(module_edges_occ.keys())
        if gained_module:
            print(f"    NEW module edges gained: {len(gained_module)}")
            for i, edge in enumerate(sorted(gained_module)[:20]):
                w = module_edges_combined[edge]
                print(f"      [{i+1}] {edge[0]}  <->  {edge[1]}  (weight={w})")

    # --- Unresolved symbols (even after combining) ---
    still_unresolved = defaultdict(int)
    for ref_file, sym, roles in all_refs:
        if combined_defs.get(sym) is None:
            still_unresolved[sym] += 1

    if still_unresolved:
        top_unresolved = sorted(still_unresolved.items(), key=lambda x: -x[1])[:15]
        print(f"\n--- Top Unresolved Symbols (even after fix) ---")
        print(f"  Total unique unresolved symbols: {len(still_unresolved)}")
        for i, (sym, count) in enumerate(top_unresolved):
            print(f"  [{i+1}] refs={count:>4}  {sym[:140]}")

    # --- Per-file cross-file edge analysis ---
    print(f"\n--- Per-file cross-file edges ---")
    file_cross_refs = defaultdict(int)
    file_same_refs = defaultdict(int)
    file_local_count = defaultdict(int)
    for doc in index.documents:
        for occ in doc.occurrences:
            if not occ.symbol:
                continue
            if _is_local(occ.symbol):
                file_local_count[doc.relative_path] += 1
                continue
            if (occ.symbol_roles & 0x1):
                continue
            def_file = combined_defs.get(occ.symbol)
            if def_file and def_file != doc.relative_path:
                file_cross_refs[doc.relative_path] += 1
            elif def_file:
                file_same_refs[doc.relative_path] += 1

    zero_cross = [f for f in all_files if file_cross_refs[f] == 0]
    has_cross = [f for f in all_files if file_cross_refs[f] > 0]
    print(f"Files with cross-file refs: {len(has_cross)} / {len(all_files)}")
    print(f"Files with 0 cross-file refs: {len(zero_cross)} / {len(all_files)}")

    if has_cross:
        print(f"\nFiles WITH cross-file refs:")
        for f in sorted(has_cross, key=lambda x: -file_cross_refs[x]):
            print(f"  cross={file_cross_refs[f]:>4}  same={file_same_refs[f]:>4}  local={file_local_count[f]:>4}  {f}")

    if zero_cross and len(zero_cross) <= 30:
        print(f"\nFiles WITHOUT cross-file refs:")
        for f in sorted(zero_cross):
            print(f"  same={file_same_refs[f]:>4}  local={file_local_count[f]:>4}  {f}")

    # --- File-level summary ---
    print(f"\n--- File Summary ---")
    print(f"Total files in index: {len(all_files)}")
    files_with_zero_occs = [f for f in all_files if file_occ_counts.get(f, 0) == 0]
    files_with_zero_syms = [f for f in all_files if file_sym_counts.get(f, 0) == 0]
    print(f"Files with 0 occurrences: {len(files_with_zero_occs)}")
    print(f"Files with 0 symbols: {len(files_with_zero_syms)}")

    # Show top files by occurrence count
    top_files = sorted(file_occ_counts.items(), key=lambda x: -x[1])[:10]
    print(f"\nTop 10 files by occurrence count:")
    for f, count in top_files:
        sym_count = file_sym_counts.get(f, 0)
        print(f"  {count:>5} occs  {sym_count:>4} syms  {f}")


def drill_down_file(scip_path: str, target_file: str):
    """Show all non-local occurrences for a specific file."""
    index = Index()
    with open(scip_path, "rb") as f:
        index.ParseFromString(f.read())

    # First collect ALL definitions from the entire index
    all_defs = {}
    all_kinds = {}
    for doc in index.documents:
        for sym_info in doc.symbols:
            if not _is_local(sym_info.symbol):
                all_defs.setdefault(sym_info.symbol, doc.relative_path)
                if sym_info.kind != 0:
                    all_kinds[sym_info.symbol] = sym_info.kind
        for occ in doc.occurrences:
            if occ.symbol and not _is_local(occ.symbol) and (occ.symbol_roles & 0x1):
                all_defs[occ.symbol] = doc.relative_path

    # Now analyze the target file
    for doc in index.documents:
        if doc.relative_path != target_file:
            continue

        print(f"\n=== Drill-down: {target_file} ===")
        print(f"Language: {doc.language}")
        print(f"Occurrences: {len(doc.occurrences)}")
        print(f"Symbols (defined here): {len(doc.symbols)}")

        # Categorize occurrences
        local_count = 0
        empty_count = 0
        defs_here = []
        same_file_refs = []
        cross_file_refs = []
        unresolved_refs = []

        for occ in doc.occurrences:
            if not occ.symbol:
                empty_count += 1
                continue
            if _is_local(occ.symbol):
                local_count += 1
                continue

            is_def = (occ.symbol_roles & 0x1) != 0

            if is_def:
                defs_here.append(occ)
            else:
                def_file = all_defs.get(occ.symbol)
                if def_file is None:
                    unresolved_refs.append(occ)
                elif def_file == target_file:
                    same_file_refs.append(occ)
                else:
                    cross_file_refs.append(occ)

        print(f"\n  Occurrence breakdown:")
        print(f"    Local symbols:     {local_count}")
        print(f"    Empty symbols:     {empty_count}")
        print(f"    Definitions:       {len(defs_here)}")
        print(f"    Same-file refs:    {len(same_file_refs)}")
        print(f"    Cross-file refs:   {len(cross_file_refs)}")
        print(f"    Unresolved refs:   {len(unresolved_refs)}")

        # Show cross-file refs grouped by target file
        if cross_file_refs:
            from collections import Counter
            target_files = Counter()
            for occ in cross_file_refs:
                target_files[all_defs[occ.symbol]] += 1
            print(f"\n  Cross-file reference targets:")
            for tf, count in target_files.most_common(20):
                print(f"    -> {tf}  ({count} refs)")

        # Show sample cross-file refs
        if cross_file_refs:
            print(f"\n  Sample cross-file refs (first 10):")
            for occ in cross_file_refs[:10]:
                def_file = all_defs[occ.symbol]
                kind = all_kinds.get(occ.symbol, "?")
                roles = occ.symbol_roles
                print(f"    roles={roles:#04x} kind={kind:>3}  -> {def_file}")
                print(f"      sym={occ.symbol[:120]}")

        # Show sample unresolved refs
        if unresolved_refs:
            from collections import Counter
            unresolved_prefixes = Counter()
            for occ in unresolved_refs:
                # Get the package portion of the symbol
                parts = occ.symbol.split(" ")
                prefix = " ".join(parts[:4]) if len(parts) >= 4 else occ.symbol
                unresolved_prefixes[prefix] += 1
            print(f"\n  Unresolved ref packages:")
            for prefix, count in unresolved_prefixes.most_common(20):
                print(f"    {prefix}  ({count} refs)")

        # Show symbols defined here
        if doc.symbols:
            print(f"\n  Symbols defined here (first 15):")
            for sym_info in doc.symbols[:15]:
                kind_str = sym_info.kind if sym_info.kind != 0 else "?"
                rels = len(sym_info.relationships)
                print(f"    kind={kind_str:>3}  rels={rels}  {sym_info.symbol[:120]}")
                for rel in sym_info.relationships:
                    print(f"        -> rel: impl={rel.is_implementation} ref={rel.is_reference} "
                          f"typedef={rel.is_type_definition} def={rel.is_definition}")
                    print(f"           sym={rel.symbol[:120]}")

        # Show ALL occurrences for first N lines (to see imports)
        print(f"\n  All occurrences on first 30 lines (including locals):")
        early_occs = [occ for occ in doc.occurrences if occ.range and occ.range[0] < 30]
        for occ in early_occs:
            line = occ.range[0] if occ.range else "?"
            roles_hex = f"{occ.symbol_roles:#04x}"
            is_def = "DEF" if (occ.symbol_roles & 0x1) else "ref"
            is_import = "IMP" if (occ.symbol_roles & 0x2) else "   "
            is_local = "LOCAL" if occ.symbol and occ.symbol.startswith("local ") else "     "
            def_file = all_defs.get(occ.symbol, "-")
            print(f"    L{line:>3} {roles_hex} {is_def} {is_import} {is_local}  "
                  f"sym={occ.symbol[:90]}")
            if def_file != "-" and def_file != target_file and not occ.symbol.startswith("local "):
                print(f"         -> defined in: {def_file}")

        # Check local symbol relationships
        local_syms_with_rels = [s for s in doc.symbols if s.symbol.startswith("local ") and s.relationships]
        print(f"\n  Local symbols with relationships: {len(local_syms_with_rels)}")
        for sym_info in local_syms_with_rels[:20]:
            print(f"    {sym_info.symbol}: {len(sym_info.relationships)} rel(s)")
            for rel in sym_info.relationships:
                rel_def_file = all_defs.get(rel.symbol, "???")
                print(f"      -> impl={rel.is_implementation} ref={rel.is_reference} "
                      f"typedef={rel.is_type_definition} def={rel.is_definition}")
                print(f"         target_sym={rel.symbol[:100]}")
                print(f"         defined_in={rel_def_file}")

        return

    print(f"File not found: {target_file}")


def list_files(scip_path: str):
    """List all files in the SCIP index."""
    index = Index()
    with open(scip_path, "rb") as f:
        index.ParseFromString(f.read())
    for doc in index.documents:
        print(f"  {len(doc.occurrences):>5} occs  {len(doc.symbols):>4} syms  {doc.relative_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python debug_scip.py <scip-file> [dir-prefix]           # full analysis")
        print("  python debug_scip.py <scip-file> --file <relative-path>  # drill into one file")
        print("  python debug_scip.py <scip-file> --list                  # list all files")
        sys.exit(1)

    scip_file = sys.argv[1]

    if len(sys.argv) >= 3 and sys.argv[2] == "--list":
        list_files(scip_file)
    elif len(sys.argv) >= 4 and sys.argv[2] == "--file":
        drill_down_file(scip_file, sys.argv[3])
    else:
        dir_prefix = sys.argv[2] if len(sys.argv) > 2 else None
        analyze(scip_file, dir_prefix)
