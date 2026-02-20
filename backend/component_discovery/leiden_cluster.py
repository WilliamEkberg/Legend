# Doc: Natural_Language_Code/component_discovery/info_component_discovery.md
"""
Leiden algorithm wrapper.

Converts a networkx graph to igraph, runs Leiden clustering
with RBConfigurationVertexPartition, and maps results back to file paths.
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
