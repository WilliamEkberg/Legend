#!/usr/bin/env python3
"""
Benchmark: compare old vs new graph building + clustering pipeline.

Runs both approaches on real .scip files and reports:
- Edge counts (before/after S1+S3 changes)
- Leiden wall-clock time
- Cluster count
- Modularity Q

Usage:
    cd backend
    python tests/benchmark_scalability.py [scip_path ...]

If no paths given, uses output/*.scip from the repo root.
"""

import math
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import networkx as nx
from component_discovery.scip_filter import parse_scip_for_module
from component_discovery.test_filter import split_source_and_test
from component_discovery.graph_builder import (
    build_file_graph,
    add_directory_affinity,
    dampen_hubs,
    auto_resolution,
    strip_virtual_nodes,
    CALL_WEIGHT,
    IMPORT_WEIGHT,
    INHERITANCE_WEIGHT,
)
from component_discovery.leiden_cluster import run_leiden, run_leiden_targeted
from component_discovery.partitioner import partition_files


# ── Old pipeline (pre-S1/S3): linear weights, all-pairs affinity ──

def _old_build_file_graph(parsed: dict, source_files: set[str]) -> nx.Graph:
    """Original build_file_graph with linear (non-log) weights, no noise filter."""
    graph = nx.Graph()
    for f in source_files:
        graph.add_node(f)

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


def _old_add_directory_affinity(graph: nx.Graph) -> nx.Graph:
    """Original O(k^2) all-pairs directory affinity."""
    from pathlib import PurePosixPath

    DIRECTORY_WEIGHT_BASE = 2.0
    DIRECTORY_SCALE_THRESHOLD = 4

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


def compute_modularity(graph: nx.Graph, clusters: dict[str, int]) -> float:
    """Compute Newman modularity Q for a given clustering."""
    if graph.number_of_edges() == 0:
        return 0.0

    # Build community list for networkx
    communities = defaultdict(set)
    for node, cid in clusters.items():
        if node in graph:
            communities[cid].add(node)

    community_list = list(communities.values())
    if len(community_list) <= 1:
        return 0.0

    try:
        return nx.algorithms.community.modularity(graph, community_list, weight="weight")
    except Exception:
        return 0.0


def run_benchmark(scip_paths: list[str]):
    """Run old vs new pipeline on SCIP data and compare metrics."""
    print("=" * 70)
    print("SCALABILITY BENCHMARK: Old vs New Pipeline")
    print("=" * 70)

    # Parse SCIP
    print(f"\nParsing SCIP: {scip_paths}")
    parsed = parse_scip_for_module(scip_paths, [""])  # empty prefix = all files

    all_files = parsed["files"]
    source_files, test_files = split_source_and_test(all_files)

    print(f"  Files: {len(all_files)} total, {len(source_files)} source, {len(test_files)} test")
    print(f"  Edges: {len(parsed['call_edges'])} call, {len(parsed['import_edges'])} import, "
          f"{len(parsed['inheritance_edges'])} inheritance")

    # ── OLD PIPELINE ──
    print(f"\n{'─' * 35}")
    print("OLD PIPELINE (linear weights, all-pairs affinity, auto_resolution)")
    print(f"{'─' * 35}")

    t0 = time.perf_counter()
    old_graph = _old_build_file_graph(parsed, source_files)
    old_graph_edges_structural = old_graph.number_of_edges()
    old_graph = _old_add_directory_affinity(old_graph)
    old_graph_edges_total = old_graph.number_of_edges()
    old_graph = dampen_hubs(old_graph)
    t_graph_old = time.perf_counter() - t0

    print(f"  Graph: {old_graph.number_of_nodes()} nodes, "
          f"{old_graph_edges_structural} structural edges, "
          f"{old_graph_edges_total} total edges (after affinity)")
    print(f"  Graph build time: {t_graph_old:.3f}s")

    resolution = auto_resolution(old_graph)
    print(f"  Resolution: {resolution:.3f}")

    t0 = time.perf_counter()
    old_clusters = run_leiden(old_graph, resolution=resolution)
    t_leiden_old = time.perf_counter() - t0

    old_n_clusters = len(set(old_clusters.values()))
    # Compute modularity on structural graph only (same basis as new pipeline)
    old_structural_graph = _old_build_file_graph(parsed, source_files)
    old_modularity = compute_modularity(old_structural_graph, old_clusters)

    print(f"  Leiden time: {t_leiden_old:.3f}s")
    print(f"  Clusters: {old_n_clusters}")
    print(f"  Modularity Q: {old_modularity:.4f}")

    # ── NEW PIPELINE ──
    print(f"\n{'─' * 35}")
    print("NEW PIPELINE (log weights, noise filter, star topology, targeted resolution, partitioning)")
    print(f"{'─' * 35}")

    target = min(25, max(3, len(source_files) // 2000))
    print(f"  Target components: {target}")

    # Partitioning
    partitions = partition_files(source_files)
    print(f"  Partitions: {len(partitions)} "
          f"(sizes: {sorted([len(p) for p in partitions], reverse=True)[:10]}{'...' if len(partitions) > 10 else ''})")

    all_clusters = {}
    total_structural_edges = 0
    total_edges = 0
    total_virtual_nodes = 0
    cluster_id_offset = 0
    total_leiden_time = 0.0
    t_graph_new_start = time.perf_counter()

    for part_idx, partition in enumerate(partitions):
        # Build graph with log weights + noise filter
        graph = build_file_graph(parsed, partition)
        structural = graph.number_of_edges()
        total_structural_edges += structural

        # Star topology affinity
        graph, virtual_nodes = add_directory_affinity(graph)
        total_edges += graph.number_of_edges()
        total_virtual_nodes += len(virtual_nodes)

        # Hub dampening
        graph = dampen_hubs(graph)

        # Targeted Leiden
        target_per_partition = max(2, target * len(partition) // len(source_files))

        t0 = time.perf_counter()
        if graph.number_of_nodes() <= 1:
            clusters = {f: cluster_id_offset for f in partition}
        else:
            clusters = run_leiden_targeted(graph, target_components=target_per_partition, seed=42)
            clusters = strip_virtual_nodes(clusters, virtual_nodes)
        total_leiden_time += time.perf_counter() - t0

        for f, c in clusters.items():
            all_clusters[f] = c + cluster_id_offset
        if clusters:
            cluster_id_offset += max(clusters.values()) + 1

    t_graph_new = time.perf_counter() - t_graph_new_start - total_leiden_time

    new_n_clusters = len(set(all_clusters.values()))

    # Compute modularity on the structural graph only (no virtual nodes)
    # Use the new log-weighted graph for a fair comparison
    full_new_graph = build_file_graph(parsed, source_files)
    new_modularity = compute_modularity(full_new_graph, all_clusters)

    print(f"  Virtual nodes created: {total_virtual_nodes}")
    print(f"  Structural edges: {total_structural_edges} (old: {old_graph_edges_structural}, "
          f"{'↓' if total_structural_edges < old_graph_edges_structural else '↑'}"
          f"{abs(total_structural_edges - old_graph_edges_structural)})")
    print(f"  Total edges (with affinity): {total_edges} (old: {old_graph_edges_total}, "
          f"{'↓' if total_edges < old_graph_edges_total else '↑'}"
          f"{abs(total_edges - old_graph_edges_total)})")
    print(f"  Graph build time: {t_graph_new:.3f}s (old: {t_graph_old:.3f}s)")
    print(f"  Leiden time: {total_leiden_time:.3f}s (old: {t_leiden_old:.3f}s)")
    print(f"  Clusters: {new_n_clusters} (old: {old_n_clusters}, target: {target})")
    print(f"  Modularity Q: {new_modularity:.4f} (old: {old_modularity:.4f})")

    # ── COMPARISON SUMMARY ──
    print(f"\n{'=' * 70}")
    print("COMPARISON SUMMARY")
    print(f"{'=' * 70}")

    edge_reduction = (1 - total_edges / old_graph_edges_total) * 100 if old_graph_edges_total else 0
    leiden_speedup = t_leiden_old / total_leiden_time if total_leiden_time > 0 else float("inf")

    print(f"  Edge reduction:     {edge_reduction:+.1f}% ({old_graph_edges_total} → {total_edges})")
    print(f"  Leiden speedup:     {leiden_speedup:.1f}x ({t_leiden_old:.3f}s → {total_leiden_time:.3f}s)")
    print(f"  Cluster count:      {old_n_clusters} → {new_n_clusters} (target: {target})")
    print(f"  Modularity Q:       {old_modularity:.4f} → {new_modularity:.4f}")

    ok = True
    issues = []

    if new_n_clusters > 50:
        issues.append(f"Too many clusters: {new_n_clusters} (want <=25)")
        ok = False
    if new_modularity < 0.1:
        issues.append(f"Low modularity: {new_modularity:.4f}")
        ok = False
    if total_edges > old_graph_edges_total:
        issues.append(f"More edges than old pipeline: {total_edges} > {old_graph_edges_total}")
        ok = False

    if ok:
        print(f"\n  ✓ All checks passed")
    else:
        print(f"\n  Issues:")
        for issue in issues:
            print(f"    ✗ {issue}")

    return ok


if __name__ == "__main__":
    if len(sys.argv) > 1:
        paths = sys.argv[1:]
    else:
        repo_root = Path(__file__).resolve().parent.parent.parent
        output_dir = repo_root / "output"
        paths = sorted(str(p) for p in output_dir.glob("*.scip"))
        if not paths:
            print(f"No .scip files found in {output_dir}")
            print("Usage: python tests/benchmark_scalability.py <scip_path> [scip_path ...]")
            sys.exit(1)

    ok = run_benchmark(paths)
    sys.exit(0 if ok else 1)
