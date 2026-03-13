# Doc: Natural_Language_Code/component_discovery/info_component_discovery.md
"""
Build a networkx graph from module-scoped SCIP edges.

- Nodes: files
- Edges: weighted sum of call (1.0) + import (0.3) + inheritance (0.5)
- Logarithmic edge weighting: log2(1 + count) compresses high-count edges
- Noise filtering: drops low-weight edges between low-degree nodes
- Directory affinity: star topology for large dirs, all-pairs for small
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

STAR_TOPOLOGY_THRESHOLD = 10  # Dirs above this use star topology instead of all-pairs
NOISE_THRESHOLD = 0.5         # Edges below this weight between low-degree nodes are dropped
SPARSE_DEGREE_THRESHOLD = 6.0 # Skip noise filter when avg structural degree is below this


def build_file_graph(parsed: dict, source_files: set[str]) -> nx.Graph:
    """Build undirected weighted graph from SCIP edges (log-weighted, noise-filtered)."""
    graph = nx.Graph()

    for file_path in source_files:
        graph.add_node(file_path)

    combined = defaultdict(float)
    for (a, b), count in parsed["call_edges"].items():
        if a in source_files and b in source_files:
            combined[(a, b)] += math.log2(1 + count) * CALL_WEIGHT
    for (a, b), count in parsed["import_edges"].items():
        if a in source_files and b in source_files:
            combined[(a, b)] += math.log2(1 + count) * IMPORT_WEIGHT
    for (a, b), count in parsed["inheritance_edges"].items():
        if a in source_files and b in source_files:
            combined[(a, b)] += math.log2(1 + count) * INHERITANCE_WEIGHT

    if combined:
        degree = defaultdict(int)
        for (a, b) in combined:
            degree[a] += 1
            degree[b] += 1

        avg_degree = sum(degree.values()) / len(degree) if degree else 0.0
        graph.graph["avg_structural_degree"] = avg_degree

        if degree and avg_degree >= SPARSE_DEGREE_THRESHOLD:
            sorted_degs = sorted(degree.values())
            median_deg = sorted_degs[len(sorted_degs) // 2]

            for (a, b), weight in list(combined.items()):
                if weight < NOISE_THRESHOLD and degree[a] <= median_deg and degree[b] <= median_deg:
                    del combined[(a, b)]

    for (a, b), weight in combined.items():
        graph.add_edge(a, b, weight=weight)

    return graph


def add_directory_affinity(graph: nx.Graph) -> tuple[nx.Graph, set[str]]:
    """Add directory affinity edges. Small dirs: all-pairs. Large dirs: star topology via virtual centroid."""
    dir_groups = defaultdict(list)
    for node in graph.nodes():
        if node.startswith("__dir__/"):
            continue
        parent = str(PurePosixPath(node).parent)
        dir_groups[parent].append(node)

    virtual_nodes = set()

    for dir_path, files in dir_groups.items():
        n = len(files)
        if n < 2:
            continue

        if n <= STAR_TOPOLOGY_THRESHOLD:
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
        else:
            centroid = f"__dir__/{dir_path}"
            virtual_nodes.add(centroid)
            graph.add_node(centroid)
            weight = DIRECTORY_WEIGHT_BASE / math.log2(n)

            for file_node in files:
                if graph.has_edge(file_node, centroid):
                    graph[file_node][centroid]["weight"] += weight
                else:
                    graph.add_edge(file_node, centroid, weight=weight)

    return graph, virtual_nodes


def strip_virtual_nodes(clusters: dict[str, int], virtual_nodes: set[str]) -> dict[str, int]:
    """Remove virtual directory centroid nodes from cluster assignments."""
    return {f: c for f, c in clusters.items() if f not in virtual_nodes}


def dampen_hubs(graph: nx.Graph) -> nx.Graph:
    """Reduce edge weights for hub nodes (>90th percentile degree)."""
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
    """Compute Leiden resolution scaled to graph size: base * (1 + 0.5 * log2(n/50)), capped at 2x."""
    n = graph.number_of_nodes()
    if n <= 50:
        return base
    scaled = base * (1.0 + 0.5 * math.log2(n / 50))
    return min(scaled, base * 2.0)
