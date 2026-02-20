# Doc: Natural_Language_Code/component_discovery/info_component_discovery.md
"""
LLM-based semantic labeling of component edges.

Pure in-memory function: takes aggregated edge dicts and component dicts,
returns the same edges with a 'label' key added to each edge's metadata.
Single batched LLM call per invocation. On failure, edges get label="".
"""

from component_discovery.llm_client import LLMClient

MAX_EDGES_FOR_PROMPT = 30


def label_component_edges(
    edges: list[dict],
    components: list[dict],
    client: LLMClient,
    log_fn: callable = print,
) -> list[dict]:
    """
    Label component edges using a single batched LLM call.

    Args:
        edges:      Edge dicts from aggregate_component_edges (must include
                    'source', 'target', 'edge_type', 'weight', 'metadata')
        components: Component dicts (must include 'name', 'purpose')
        client:     LLMClient instance
        log_fn:     Logging callback

    Returns:
        The same edge list with metadata['label'] populated.
    """
    if not edges:
        return edges

    # Build component summary (only components that appear in edges)
    used_names = set()
    for e in edges:
        used_names.add(e["source"])
        used_names.add(e["target"])

    comp_by_name = {c["name"]: c for c in components}
    comp_lines = [
        f"- {name}: {comp_by_name[name].get('purpose') or 'no description'}"
        for name in sorted(used_names)
        if name in comp_by_name
    ]

    # Truncate to top edges by weight if too many
    edges_for_prompt = edges[:MAX_EDGES_FOR_PROMPT]

    # Build edge list with edge type
    edge_lines = []
    for e in edges_for_prompt:
        edge_lines.append(
            f"{e['source']} --[{e['edge_type']}]--> {e['target']} (weight: {e['weight']})"
        )

    prompt = (
        "Label these component relationships. For each edge write a concise phrase "
        "(3–8 words) explaining why the source depends on the target.\n\n"
        "Components:\n"
        + "\n".join(comp_lines)
        + "\n\nEdges:\n"
        + "\n".join(f"{i + 1}. {line}" for i, line in enumerate(edge_lines))
        + "\n\nReturn JSON only:\n"
        '{"edges": [{"source": "comp_name", "target": "comp_name", "edge_type": "call", "label": "..."}]}'
    )

    try:
        result = client.query(prompt)
        raw_labels = result.get("edges", [])
    except Exception as e:
        log_fn(f"[Edges] Warning: LLM labeling failed: {e}")
        # Graceful failure: add empty labels to all edges
        for edge in edges:
            edge["metadata"]["label"] = ""
        return edges

    # Build lookup: (source, target, edge_type) -> label
    label_map = {}
    for item in raw_labels:
        src = item.get("source", "")
        tgt = item.get("target", "")
        etype = item.get("edge_type", "")
        label = (item.get("label") or "").strip()
        if label:
            label_map[(src, tgt, etype)] = label

    labeled = 0
    for edge in edges:
        label = label_map.get(
            (edge["source"], edge["target"], edge["edge_type"]), ""
        )
        edge["metadata"]["label"] = label
        if label:
            labeled += 1

    log_fn(f"[Edges] Labeled {labeled}/{len(edges)} edge(s)")
    return edges
