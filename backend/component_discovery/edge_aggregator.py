# Doc: Natural_Language_Code/component_discovery/info_component_discovery.md
"""
Aggregate file-level SCIP edges to component-level edges.

For each file-level edge (call/import/inheritance), look up which components
the source and target files belong to. Each edge type produces a separate
component-level edge per pair. Weights are normalized by component size
(geometric mean) then scaled to 0–10 range.
"""

from collections import defaultdict


def aggregate_component_edges(
    components: list[dict],
    call_edges: dict,
    import_edges: dict,
    inheritance_edges: dict,
) -> list[dict]:
    """
    Aggregate file-level edges into typed, size-normalized component edges.

    Args:
        components: list of component dicts with 'name' and 'files'
        call_edges: {(file_a, file_b): count} from SCIP
        import_edges: {(file_a, file_b): count} from SCIP
        inheritance_edges: {(file_a, file_b): count} from SCIP

    Returns:
        list of edge dicts: {source, target, edge_type, weight, metadata}
        where edge_type is "call", "import", or "inheritance" and weight
        is normalized by geometric mean of component file counts, then
        scaled to 0–10 (strongest edge = 10).
    """
    # Build file -> component name mapping and component file counts
    file_to_comp = {}
    comp_file_counts: dict[str, int] = {}
    for comp in components:
        comp_file_counts[comp["name"]] = len(comp["files"])
        for f in comp["files"]:
            file_to_comp[f] = comp["name"]

    # Accumulate counts per (sorted component pair, edge_type)
    pair_weights: dict[tuple, float] = defaultdict(float)

    type_map = [
        (call_edges, "call"),
        (import_edges, "import"),
        (inheritance_edges, "inheritance"),
    ]

    for edge_set, edge_type in type_map:
        for (a, b), count in edge_set.items():
            comp_a = file_to_comp.get(a)
            comp_b = file_to_comp.get(b)
            if comp_a and comp_b and comp_a != comp_b:
                key = (*sorted([comp_a, comp_b]), edge_type)
                pair_weights[key] += float(count)

    # Build edges with size-normalized weights
    edges = []
    for (src, tgt, etype), raw_weight in pair_weights.items():
        src_n = max(comp_file_counts.get(src, 1), 1)
        tgt_n = max(comp_file_counts.get(tgt, 1), 1)
        normalized = raw_weight / (src_n * tgt_n) ** 0.5
        edges.append({
            "source": src,
            "target": tgt,
            "edge_type": etype,
            "weight": normalized,
            "metadata": {},
        })

    # Scale to 0–10 range so the weight slider is intuitive
    if edges:
        max_w = max(e["weight"] for e in edges)
        if max_w > 0:
            for e in edges:
                e["weight"] = round(e["weight"] / max_w * 10, 1)
        else:
            for e in edges:
                e["weight"] = 0.0

    edges.sort(key=lambda e: e["weight"], reverse=True)
    return edges
