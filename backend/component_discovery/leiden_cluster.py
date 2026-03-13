# Doc: Natural_Language_Code/component_discovery/info_component_discovery.md
"""
Leiden clustering wrapper with targeted resolution sweep.
"""

import igraph as ig
import leidenalg as la
import networkx as nx

RESOLUTION = 1.5


def run_leiden(graph: nx.Graph, resolution: float = RESOLUTION, seed: int = 42) -> dict[str, int]:
    """
    Run Leiden clustering on a networkx graph.

    Args:
        graph: networkx Graph with weighted edges
        resolution: Leiden resolution parameter (higher = more clusters)
        seed: random seed for reproducibility (fixed at 42)

    Returns:
        dict mapping file_path -> cluster_id (int)
    """
    if graph.number_of_nodes() == 0:
        return {}

    if graph.number_of_nodes() == 1:
        node = list(graph.nodes())[0]
        return {node: 0}

    ig_graph = ig.Graph.from_networkx(graph)

    weights = ig_graph.es["weight"] if ig_graph.ecount() > 0 and "weight" in ig_graph.es.attributes() else None

    partition = la.find_partition(
        ig_graph,
        la.RBConfigurationVertexPartition,
        resolution_parameter=resolution,
        weights=weights,
        seed=seed,
    )

    node_names = ig_graph.vs["_nx_name"]

    return {
        node_names[i]: partition.membership[i]
        for i in range(len(node_names))
    }


def run_leiden_targeted(
    graph: nx.Graph,
    target_components: int,
    max_iterations: int = 5,
    tolerance: int = 2,
    seed: int = 42,
) -> dict[str, int]:
    """Run Leiden with binary search over resolution to hit target cluster count."""
    if graph.number_of_nodes() == 0:
        return {}

    if graph.number_of_nodes() == 1:
        node = list(graph.nodes())[0]
        return {node: 0}

    if target_components <= 1:
        return {node: 0 for node in graph.nodes()}

    ig_graph = ig.Graph.from_networkx(graph)
    weights = ig_graph.es["weight"] if ig_graph.ecount() > 0 and "weight" in ig_graph.es.attributes() else None
    node_names = ig_graph.vs["_nx_name"]

    low_res = 0.01
    high_res = 10.0
    best_result = None
    best_diff = float("inf")

    for _ in range(max_iterations):
        mid_res = (low_res + high_res) / 2.0

        partition = la.find_partition(
            ig_graph,
            la.RBConfigurationVertexPartition,
            resolution_parameter=mid_res,
            weights=weights,
            seed=seed,
        )

        n_clusters = len(set(partition.membership))
        diff = abs(n_clusters - target_components)

        if diff < best_diff:
            best_diff = diff
            best_result = {
                node_names[i]: partition.membership[i]
                for i in range(len(node_names))
            }

        if diff <= tolerance:
            break

        if n_clusters > target_components:
            high_res = mid_res
        else:
            low_res = mid_res

    return best_result
