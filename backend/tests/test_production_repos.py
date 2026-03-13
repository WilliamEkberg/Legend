"""
Test the component discovery pipeline on production-grade repos.
Runs SCIP parsing + graph building + partitioning + Leiden clustering.
Exercises the full pipeline including adaptive noise filter, adaptive
partitioning, and directory-proximity merge. No DB or LLM needed.
"""

import sys
import os
import time
from collections import Counter, defaultdict
from pathlib import PurePosixPath

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from component_discovery.scip_parser import load_scip_index
from component_discovery.scip_filter import parse_scip_for_module
from component_discovery.test_filter import split_source_and_test
from component_discovery.graph_builder import (
    build_file_graph, add_directory_affinity, dampen_hubs, strip_virtual_nodes,
)
from component_discovery.leiden_cluster import run_leiden, run_leiden_targeted
from component_discovery.partitioner import partition_files
from component_discovery.component_namer import name_components

# Pipeline constants (matching pipeline.py)
MAX_COMPONENTS = 25
MIN_COMPONENTS = 3
FILES_PER_COMPONENT = 1000


def _merge_small_clusters(clusters, target_components):
    """
    Iteratively merge smallest cluster into most directory-similar cluster.
    Copy of pipeline._merge_small_clusters for standalone testing.
    """
    cluster_files = defaultdict(set)
    for f, c in clusters.items():
        cluster_files[c].add(f)

    def _dir_prefixes(files):
        prefixes = set()
        for f in files:
            parts = PurePosixPath(f).parts
            if len(parts) > 2:
                prefixes.add("/".join(parts[:2]))
            elif len(parts) > 1:
                prefixes.add(parts[0])
        return prefixes

    while len(cluster_files) > target_components:
        smallest_id = min(cluster_files, key=lambda c: len(cluster_files[c]))
        smallest_files = cluster_files[smallest_id]
        smallest_prefixes = _dir_prefixes(smallest_files)

        best_id = None
        best_score = -1.0
        best_size = float("inf")

        for cid, cfiles in cluster_files.items():
            if cid == smallest_id:
                continue
            other_prefixes = _dir_prefixes(cfiles)
            if smallest_prefixes or other_prefixes:
                intersection = len(smallest_prefixes & other_prefixes)
                union = len(smallest_prefixes | other_prefixes)
                jaccard = intersection / union if union else 0.0
            else:
                jaccard = 0.0
            if jaccard > best_score or (jaccard == best_score and len(cfiles) < best_size):
                best_score = jaccard
                best_id = cid
                best_size = len(cfiles)

        if best_id is None:
            break
        cluster_files[best_id].update(smallest_files)
        del cluster_files[smallest_id]

    result = {}
    for cid, files in cluster_files.items():
        for f in files:
            result[f] = cid
    return result


def run_pipeline_on_scip(repo_name: str, scip_paths: list[str]):
    """Run the full component discovery pipeline on .scip files."""
    print(f"\n{'='*70}")
    print(f"  {repo_name}")
    print(f"{'='*70}")

    # Step 1: Discover top-level dirs from SCIP, then parse with those as module_dirs
    t0 = time.time()
    top_dirs = set()
    valid_scip_paths = []
    for sp in scip_paths:
        idx = load_scip_index(sp)
        if idx is None:
            print(f"  WARNING: Skipping {os.path.basename(sp)}: failed to parse")
            continue
        for doc in idx.documents:
            parts = doc.relative_path.split("/")
            if len(parts) > 1:
                top_dirs.add(parts[0])
        valid_scip_paths.append(sp)
        del idx  # free memory

    if not valid_scip_paths:
        print("  ERROR: No valid .scip files!")
        return None

    module_dirs = sorted(top_dirs) if top_dirs else ["."]
    parsed = parse_scip_for_module(valid_scip_paths, module_dirs)
    t_parse = time.time() - t0

    all_files = parsed["files"]
    source_files, test_files = split_source_and_test(all_files)

    print(f"  Parse:  {t_parse:.1f}s | {len(all_files)} files total, "
          f"{len(source_files)} source, {len(test_files)} test")
    print(f"  Edges:  {len(parsed['call_edges'])} call, "
          f"{len(parsed['import_edges'])} import, "
          f"{len(parsed['inheritance_edges'])} inheritance")

    if not source_files:
        print("  ERROR: No source files found!")
        return

    # Step 2: Compute target components (NEW: FILES_PER_COMPONENT = 1000)
    target_components = min(MAX_COMPONENTS, max(MIN_COMPONENTS, len(source_files) // FILES_PER_COMPONENT))
    print(f"  Target: {target_components} components")

    # Step 2b: Partition files (NEW: adaptive partitioning with target_components)
    t0 = time.time()
    partitions = partition_files(source_files, target_components=target_components)
    t_partition = time.time() - t0
    partition_sizes = sorted([len(p) for p in partitions], reverse=True)
    print(f"  Partitions: {len(partitions)} (sizes: {partition_sizes[:10]}{'...' if len(partitions) > 10 else ''}) [{t_partition:.2f}s]")

    # Step 3-5: Build graph + cluster per partition
    t0 = time.time()
    all_clusters = {}
    all_virtual_nodes = set()
    total_edges = 0
    cluster_id_offset = 0
    errors = []

    for part_idx, partition in enumerate(partitions):
        try:
            # Build graph (NEW: adaptive noise filter)
            graph = build_file_graph(parsed, partition)
            avg_deg = graph.graph.get("avg_structural_degree", 0.0)

            # Directory affinity
            graph, virtual_nodes = add_directory_affinity(graph)
            all_virtual_nodes.update(virtual_nodes)

            # Hub dampening
            graph = dampen_hubs(graph)
            total_edges += graph.number_of_edges()

            if len(partitions) > 1 and part_idx < 5:
                print(f"    [partition {part_idx+1}/{len(partitions)}] "
                      f"{graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges, "
                      f"avg_degree={avg_deg:.1f}")

            # Leiden clustering
            target_per_partition = max(2, target_components * len(partition) // len(source_files))

            if graph.number_of_nodes() <= 1:
                clusters = {f: cluster_id_offset for f in partition}
            else:
                clusters = run_leiden_targeted(
                    graph,
                    target_components=target_per_partition,
                    seed=42,
                )
                clusters = strip_virtual_nodes(clusters, virtual_nodes)

            for f, c in clusters.items():
                all_clusters[f] = c + cluster_id_offset

            if clusters:
                cluster_id_offset += max(clusters.values()) + 1

        except Exception as e:
            errors.append(f"Partition {part_idx} ({len(partition)} files): {e}")
            for f in partition:
                all_clusters[f] = cluster_id_offset
            cluster_id_offset += 1

    t_cluster = time.time() - t0

    clusters = all_clusters
    n_raw_clusters = len(set(clusters.values()))

    print(f"  Graph:  {total_edges} edges [{t_cluster:.1f}s]")
    print(f"  Clusters: {n_raw_clusters} raw clusters (target={target_components})")

    # NEW: Step 5b — directory-proximity merge
    n_before_merge = n_raw_clusters
    if n_raw_clusters > target_components * 3:
        t0 = time.time()
        clusters = _merge_small_clusters(clusters, target_components)
        t_merge = time.time() - t0
        n_merged = len(set(clusters.values()))
        print(f"  Directory merge: {n_raw_clusters} -> {n_merged} clusters [{t_merge:.2f}s]")
    else:
        n_merged = n_raw_clusters
        print(f"  Directory merge: skipped (raw clusters {n_raw_clusters} <= {target_components * 3})")

    n_clusters = len(set(clusters.values()))

    if errors:
        print(f"  ERRORS ({len(errors)}):")
        for e in errors:
            print(f"    - {e}")

    # Step 6: Name components
    try:
        t0 = time.time()
        names = name_components(clusters)
        t_name = time.time() - t0

        # Show component summary
        cluster_sizes = Counter(clusters.values())
        named_clusters = []
        for cid, size in cluster_sizes.most_common(30):
            # Find the name for files in this cluster
            sample_file = next(f for f, c in clusters.items() if c == cid)
            cname = names.get(sample_file, f"Cluster-{cid}")
            named_clusters.append(f"    {cname}: {size} files")

        print(f"\n  Components ({n_clusters}):")
        for line in named_clusters:
            print(line)
    except Exception as e:
        print(f"  Naming ERROR: {e}")

    # Coverage stats
    unclustered = source_files - set(clusters.keys())
    if unclustered:
        print(f"\n  WARNING: {len(unclustered)} source files NOT clustered")

    leaked = set(clusters.keys()) - source_files
    real_leaked = {f for f in leaked if not f.startswith("__dir__/")}
    if real_leaked:
        print(f"  WARNING: {len(real_leaked)} non-source files in clusters")

    # Verdict
    in_range = MIN_COMPONENTS <= n_clusters <= MAX_COMPONENTS * 2
    print(f"\n  RESULT: {len(source_files)} files -> {n_clusters} components "
          f"(target={target_components}), {len(errors)} errors")
    print(f"  VERDICT: {'PASS' if in_range and not errors else 'FAIL'} "
          f"({'in range' if in_range else f'OUT OF RANGE {MIN_COMPONENTS}-{MAX_COMPONENTS*2}'})")
    print()
    return {
        "repo": repo_name,
        "source_files": len(source_files),
        "test_files": len(test_files),
        "n_components": n_clusters,
        "n_raw_clusters": n_before_merge,
        "target": target_components,
        "total_edges": total_edges,
        "partitions": len(partitions),
        "errors": errors,
        "in_range": in_range,
    }


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format='  [scip_parser] %(message)s')

    output_base = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                "scip-engine", "output")

    repos = {
        "appsmith": ["typescript.scip", "python.scip"],
        "nx": ["typescript.scip"],
        "grafana": ["typescript.scip", "go.scip"],
        "payload": ["typescript.scip"],
        "backstage": ["typescript.scip"],
        "medusa": ["typescript.scip"],
    }

    results = []
    for repo_name, scip_files in repos.items():
        repo_dir = os.path.join(output_base, f"{repo_name}-test")
        scip_paths = [os.path.join(repo_dir, f) for f in scip_files
                      if os.path.exists(os.path.join(repo_dir, f))]
        if not scip_paths:
            print(f"SKIP {repo_name}: no .scip files in {repo_dir}")
            continue

        result = run_pipeline_on_scip(repo_name, scip_paths)
        if result:
            results.append(result)

    # Final summary
    print("\n" + "="*70)
    print("  FINAL SUMMARY")
    print("="*70)
    all_pass = True
    for r in results:
        status = "PASS" if r["in_range"] and not r["errors"] else "FAIL"
        if status == "FAIL":
            all_pass = False
        print(f"  {r['repo']:15s} | {r['source_files']:5d} files | "
              f"raw={r['n_raw_clusters']:3d} -> final={r['n_components']:3d} components "
              f"(target={r['target']}) | {r['partitions']} partitions | {status}")
    print(f"\n  OVERALL: {'ALL PASS' if all_pass else 'SOME FAILURES'}")
