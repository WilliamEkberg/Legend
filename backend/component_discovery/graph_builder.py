# Doc: Natural_Language_Code/component_discovery/info_component_discovery.md
"""
Build a networkx graph from module-scoped SCIP edges.

- Nodes: files
- Edges: weighted sum of call (1.0) + import (0.3) + inheritance (0.5)
- Directory affinity: scaled weight between files sharing a directory
- Hub dampening: normalize edge weights for high-degree nodes
"""

import math
from collections import defaultdict
from pathlib import PurePosixPath

import networkx as nx

CALL_WEIGHT = 1.0
IMPORT_WEIGHT = 0.3
INHERITANCE_WEIGHT = 0.5

DIRECTORY_WEIGHT_BASE = 2.0
DIRECTORY_SCALE_THRESHOLD = 4  # Dirs with <= this many files get full weight
HUB_PERCENTILE = 90           # Nodes above this percentile in degree get dampened


def build_file_graph(parsed: dict, source_files: set[str]) -> nx.Graph:
    """
    Build undirected weighted graph from module-scoped SCIP edges.

    Combines call + import + inheritance edges with type-specific weights.
    Only includes files in source_files (tests excluded).

    Args:
        parsed: output from scip_filter.parse_scip_for_module()
        source_files: set of source file paths (no tests)

    Returns:
        networkx Graph with file nodes and weighted edges
    """
    graph = nx.Graph()

    for file_path in source_files:
        graph.add_node(file_path)

    # Combine all edge types with weights
    combined = defaultdict(float)
    for (a, b), count in parsed["call_edges"].items():
        if a in source_files and b in source_files:
            combined[(a, b)] += count * CALL_WEIGHT
    for (a, b), count in parsed["import_edges"].items():
        if a in source_files and b in source_files:
            combined[(a, b)] += count * IMPORT_WEIGHT
    for (a, b), count in parsed["inheritance_edges"].items():
        if a in source_files and b in source_files:
            combined[(a, b)] += count * INHERITANCE_WEIGHT

    for (a, b), weight in combined.items():
        graph.add_edge(a, b, weight=weight)

    return graph


def add_directory_affinity(graph: nx.Graph) -> nx.Graph:
    """
    Add weight between files in the same directory.

    Weight scales inversely with directory size:
    - Small dirs (<=4 files): full DIRECTORY_WEIGHT_BASE
    - Larger dirs: BASE / log2(n) — prevents large flat dirs from
      creating massive affinity cliques.
    """
    dir_groups = defaultdict(list)
    for node in graph.nodes():
        parent = str(PurePosixPath(node).parent)
        dir_groups[parent].append(node)

    for _dir, files in dir_groups.items():
        n = len(files)
        if n < 2:
            continue

        if n <= DIRECTORY_SCALE_THRESHOLD:
            weight = DIRECTORY_WEIGHT_BASE
        else:
            weight = DIRECTORY_WEIGHT_BASE / math.log2(n)

        for i, file_a in enumerate(files):
            for file_b in files[i + 1:]:
                if graph.has_edge(file_a, file_b):
                    graph[file_a][file_b]["weight"] += weight
                else:
                    graph.add_edge(file_a, file_b, weight=weight)

    return graph


def dampen_hubs(graph: nx.Graph) -> nx.Graph:
    """
    Reduce influence of hub nodes (high-degree files like utils).

    Nodes above the 90th percentile of degree have their edge weights
    divided by sqrt(degree/threshold).
    """
    degrees = [graph.degree(n) for n in graph.nodes()]
    if not degrees or max(degrees) <= 1:
        return graph

    sorted_degrees = sorted(degrees)
    idx = int(len(sorted_degrees) * HUB_PERCENTILE / 100)
    threshold = max(sorted_degrees[min(idx, len(sorted_degrees) - 1)], 2)

    for node in graph.nodes():
        degree = graph.degree(node)
        if degree <= threshold:
            continue
        factor = math.sqrt(degree / threshold)
        for neighbor in list(graph.neighbors(node)):
            graph[node][neighbor]["weight"] /= factor

    return graph


def auto_resolution(graph: nx.Graph, base: float = 1.0) -> float:
    """
    Compute Leiden resolution parameter scaled to graph size.

    base * (1 + 0.5 * log2(nodes/50)), capped at 2x base.
    """
    n = graph.number_of_nodes()
    if n <= 50:
        return base
    scaled = base * (1.0 + 0.5 * math.log2(n / 50))
    return min(scaled, base * 2.0)
