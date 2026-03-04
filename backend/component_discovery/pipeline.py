# Doc: Natural_Language_Code/component_discovery/info_component_discovery.md
"""
Pipeline orchestrator: SCIP + DB modules -> components in DB.

Phase 1 — Nodes (steps 1-10):
1. Filter SCIP to module scope
2. Split source/test files
3. Build weighted graph (call + import + inheritance)
4. Leiden clustering with auto-resolution
5. Post-cluster redistribution
6. Name components by dominant directory
7. Extract per-file metadata
8. LLM refinement per cluster
9. Reconcile misplaced files
10. Assign test files

Phase 2 — Edges (steps 11-13):
11. Aggregate: combine file edges into one edge per component pair
12. LLM label: batch label all edges in-memory
13. Write to DB: components + labeled edges together
"""

import json
import sqlite3
from collections import defaultdict
from pathlib import PurePosixPath

import db
from component_discovery.scip_filter import parse_scip_for_module
from component_discovery.test_filter import is_test_file, split_source_and_test
from component_discovery.graph_builder import (
    build_file_graph, add_directory_affinity, dampen_hubs, auto_resolution,
)
from component_discovery.leiden_cluster import run_leiden
from component_discovery.component_namer import name_components
from component_discovery.metadata_extractor import extract_file_metadata
from component_discovery.cluster_analyzer import (
    analyze_clusters, reconcile_misplaced_files,
)
from component_discovery.edge_aggregator import aggregate_component_edges
from component_discovery.edge_labeler import label_component_edges
from component_discovery.llm_client import LLMClient


def discover_components(
    conn: sqlite3.Connection,
    module: dict,
    scip_path: str | list[str],
    source_dir: str,
    client: LLMClient | None = None,
    run_id: int | None = None,
    log_fn: callable = print,
    skip_llm: bool = False,
) -> None:
    """
    Discover L3 components for a single module and write to DB.

    Args:
        conn: Database connection
        module: Module dict from get_module() (id, name, directories, etc.)
        scip_path: Path to a .scip file, or list of paths (merged across languages)
        source_dir: Path to source directory root
        client: LLMClient instance (creates one if not provided)
        run_id: Pipeline run ID
    """
    module_id = module["id"]
    module_name = module["name"]

    # Get module directories
    dirs = db.get_module_directories(conn, module_id)
    if not dirs:
        log_fn(f"[Part 2] Skipping '{module_name}': no directories in DB")
        return

    log_fn(f"[Part 2] Processing module: {module_name} ({len(dirs)} director{'y' if len(dirs) == 1 else 'ies'})")

    # ===== Phase 1 — Nodes =====
    log_fn(f"  --- Phase 1: Nodes ---")

    # Step 1: Filter SCIP to module scope
    log_fn(f"  [1/9] Filtering SCIP index to module scope...")
    parsed = parse_scip_for_module(scip_path, dirs)

    if not parsed["files"]:
        log_fn(f"[Part 2] Skipping '{module_name}': no files found in SCIP index for dirs: {dirs}")
        return

    total_files = len(parsed["files"])
    log_fn(f"  [1/9] SCIP: {total_files} files, {parsed['definitions']} definitions, "
           f"{len(parsed['call_edges'])} call edges, {len(parsed['import_edges'])} import edges, "
           f"{len(parsed['inheritance_edges'])} inheritance edges")

    # Step 2: Split source/test
    source_files, test_files = split_source_and_test(parsed["files"])

    if not source_files:
        log_fn(f"[Part 2] Skipping '{module_name}': no source files (all {len(test_files)} are test files)")
        return

    log_fn(f"  [2/9] Files: {len(source_files)} source, {len(test_files)} test")

    # Step 3: Build weighted graph
    log_fn(f"  [3/9] Building dependency graph...")
    graph = build_file_graph(parsed, source_files)

    # Step 4: Directory affinity + hub dampening
    graph = add_directory_affinity(graph)
    graph = dampen_hubs(graph)
    log_fn(f"  [4/9] Graph: {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges (affinity + hub dampening applied)")

    # Step 5: Leiden clustering
    resolution = auto_resolution(graph)
    log_fn(f"  [5/9] Leiden clustering (resolution={resolution:.3f})...")
    clusters = run_leiden(graph, resolution=resolution)
    n_raw_clusters = len(set(clusters.values()))
    log_fn(f"  [5/9] Leiden: {n_raw_clusters} initial cluster(s)")

    # Step 6: Post-cluster redistribution
    clusters_before = dict(clusters)
    clusters = _redistribute_hubs(clusters, parsed["call_edges"], source_files)
    moves = sum(1 for f, c in clusters.items() if clusters_before.get(f) != c)
    if moves:
        log_fn(f"  [6/9] Hub redistribution: {moves} file(s) moved to better-fit clusters")

    # Step 7: Name components by dominant directory
    named = name_components(clusters)

    # Group files by component name
    comp_files = defaultdict(list)
    for file_path, comp_name in named.items():
        comp_files[comp_name].append(file_path)

    # Step 8: Extract per-file metadata
    log_fn(f"  [7/9] Extracting per-file metadata...")
    file_metadata = extract_file_metadata(scip_path, source_dir, source_files)

    # Step 9: LLM refinement (or skip if skip_llm mode)
    # Convert to cluster_id -> files for analyzer
    cluster_id_files = defaultdict(list)
    for file_path, cluster_id in clusters.items():
        cluster_id_files[cluster_id].append(file_path)

    if skip_llm:
        log_fn(f"  [8/9] SKIP_LLM: Skipping LLM analysis, using placeholder names...")
        # Generate placeholder components without LLM
        components = []
        for cluster_id, files in cluster_id_files.items():
            comp_name = f"component_{cluster_id}"
            components.append({
                "name": comp_name,
                "purpose": f"[SKIP_LLM] Placeholder component containing {len(files)} file(s)",
                "files": files,
                "confidence": 0.5,
            })
        misplaced = []
    else:
        if client is None:
            client = LLMClient()
        log_fn(f"  [8/9] LLM analysis: {len(cluster_id_files)} cluster(s)...")
        components, misplaced = analyze_clusters(
            cluster_id_files, file_metadata, parsed["call_edges"], client, log_fn=log_fn
        )

    # Step 10: Reconcile misplaced files
    if misplaced:
        log_fn(f"  [8/9] Reconciling {len(misplaced)} misplaced file suggestion(s)...")
    components = reconcile_misplaced_files(components, misplaced)

    # Step 11: Assign test files
    test_assignments = _assign_test_files(
        test_files, parsed["call_edges"], components
    )
    unassigned = [f for f, c in test_assignments.items() if c == "unassigned"]
    if unassigned:
        log_fn(f"  [8/9] Test assignment: {len(test_files) - len(unassigned)}/{len(test_files)} assigned; "
               f"{len(unassigned)} unassigned (no call edges or path match)")

    # ===== Phase 2 — Edges =====
    log_fn(f"  --- Phase 2: Edges ---")

    # Step 11: Aggregate — combine file edges into one edge per component pair
    log_fn(f"  [Phase 2] Aggregating component edges...")
    l3_edges = aggregate_component_edges(
        components,
        parsed["call_edges"],
        parsed["import_edges"],
        parsed["inheritance_edges"],
    )
    log_fn(f"  [Phase 2] {len(l3_edges)} edge(s) after aggregation")

    # Step 12: LLM label — batch label all edges in-memory (skip if skip_llm mode)
    if l3_edges and client and not skip_llm:
        log_fn(f"  [Phase 2] Labeling edges...")
        l3_edges = label_component_edges(l3_edges, components, client, log_fn)
    elif l3_edges and skip_llm:
        log_fn(f"  [Phase 2] SKIP_LLM: Skipping edge labeling...")

    # Step 13: Write to DB — components + labeled edges together
    _write_to_db(conn, module_id, components, test_assignments, l3_edges, run_id)

    low_confidence = [c for c in components if (c.get("confidence") or 1.0) < 0.5]
    log_fn(f"[Part 2] Done: '{module_name}' -> {len(components)} component(s), "
           f"{len(source_files)} source files, {len(test_files)} test files, "
           f"{len(l3_edges)} L3 edges"
           + (f", {len(low_confidence)} low-confidence component(s)" if low_confidence else ""))


def discover_all_components(
    conn: sqlite3.Connection,
    scip_path: str | list[str],
    source_dir: str,
    client: LLMClient | None = None,
    log_fn: callable = print,
    skip_llm: bool = False,
) -> None:
    """
    Discover components for all eligible modules and write to DB.

    Reads modules from DB. Skips modules where classification='supporting-asset'
    or source_origin!='in-repo'.
    """
    run_id = db.start_pipeline_run(conn, "part2")

    try:
        modules = db.get_modules(conn)

        if not modules:
            log_fn("[Part 2] No modules found in database. Has Part 1 been run?")
            db.complete_pipeline_run(conn, run_id, status="failed")
            return

        log_fn(f"[Part 2] Found {len(modules)} module(s) in database")

        processed = 0
        skipped = 0
        total_components = 0
        total_edges = 0

        for module in modules:
            # Module filter per NLC doc
            if module["classification"] == "supporting-asset":
                log_fn(f"[Part 2] Skipping '{module['name']}': classification=supporting-asset")
                skipped += 1
                continue
            if module.get("source_origin") != "in-repo":
                log_fn(f"[Part 2] Skipping '{module['name']}': source_origin={module.get('source_origin')}")
                skipped += 1
                continue

            # Get directories for this module
            dirs = [
                r["path"] for r in conn.execute(
                    "SELECT path FROM module_directories WHERE module_id = ?",
                    (module["id"],)
                ).fetchall()
            ]
            module_with_dirs = dict(module)
            module_with_dirs["directories"] = dirs

            # Clear existing components for this module before re-discovering
            conn.execute("DELETE FROM components WHERE module_id = ?", (module["id"],))
            conn.commit()

            comp_before = 0

            discover_components(conn, module_with_dirs, scip_path, source_dir, client, run_id, log_fn=log_fn, skip_llm=skip_llm)

            comp_after = conn.execute(
                "SELECT COUNT(*) FROM components WHERE module_id = ?", (module["id"],)
            ).fetchone()[0]
            edge_count = conn.execute(
                "SELECT COUNT(*) FROM component_edges WHERE pipeline_run_id = ?", (run_id,)
            ).fetchone()[0]

            total_components += (comp_after - comp_before)
            total_edges = edge_count
            processed += 1

        log_fn(f"[Part 2] Pipeline complete: {processed} module(s) processed, "
               f"{skipped} skipped, {total_components} component(s) discovered, "
               f"{total_edges} L3 edge(s) written")

        db.complete_pipeline_run(conn, run_id)
    except Exception:
        db.complete_pipeline_run(conn, run_id, status="failed")
        raise


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _redistribute_hubs(
    clusters: dict[str, int],
    call_edges: dict,
    source_files: set[str],
) -> dict[str, int]:
    """
    Post-cluster: move files where >60% of call weight goes to another cluster.
    """
    MOVE_THRESHOLD = 0.6

    file_cluster_weight = defaultdict(lambda: defaultdict(float))
    for (a, b), count in call_edges.items():
        if a in clusters and b in clusters:
            ca, cb = clusters[a], clusters[b]
            file_cluster_weight[a][cb] += count
            file_cluster_weight[b][ca] += count

    moves = {}
    for file_path, cluster_weights in file_cluster_weight.items():
        own_cluster = clusters[file_path]
        total = sum(cluster_weights.values())
        if total == 0:
            continue

        own_weight = cluster_weights.get(own_cluster, 0)
        own_fraction = own_weight / total

        if own_fraction < (1.0 - MOVE_THRESHOLD):
            best_cluster = max(cluster_weights, key=cluster_weights.get)
            best_fraction = cluster_weights[best_cluster] / total
            if best_cluster != own_cluster and best_fraction >= MOVE_THRESHOLD:
                moves[file_path] = best_cluster

    result = dict(clusters)
    result.update(moves)
    return result


def _assign_test_files(
    test_files: set[str],
    all_edges: dict,
    components: list[dict],
) -> dict[str, str]:
    """
    Assign each test file to the source component it references most.

    Fallback chain:
    1. Call-edge weight to source components
    2. Path similarity
    3. "unassigned"
    """
    # Build file -> component name mapping from components
    file_to_comp = {}
    for comp in components:
        for f in comp["files"]:
            file_to_comp[f] = comp["name"]

    result = {}
    for test_file in test_files:
        # Strategy 1: call-edge weight
        comp_scores = defaultdict(float)
        for (a, b), count in all_edges.items():
            if a == test_file and b in file_to_comp:
                comp_scores[file_to_comp[b]] += count
            elif b == test_file and a in file_to_comp:
                comp_scores[file_to_comp[a]] += count

        if comp_scores:
            result[test_file] = max(comp_scores, key=comp_scores.get)
            continue

        # Strategy 2: path similarity
        match = _match_test_to_component_by_path(test_file, file_to_comp)
        if match:
            result[test_file] = match
            continue

        result[test_file] = "unassigned"

    return result


def _match_test_to_component_by_path(
    test_path: str,
    file_to_comp: dict[str, str],
) -> str | None:
    """Match a test file to a component by naming convention."""
    path = PurePosixPath(test_path)
    stem = path.stem

    candidates = []
    if stem.startswith("test_"):
        subject = stem[5:]
        candidates.append(subject)
        parts = subject.split("_")
        if len(parts) > 1:
            candidates.append(parts[0])
            candidates.append("/".join(parts))

    parent_name = path.parent.name
    if parent_name and parent_name not in ("test", "tests", "__tests__", "spec"):
        candidates.append(parent_name)

    if not candidates:
        return None

    comp_scores = defaultdict(int)
    for src_path, comp_name in file_to_comp.items():
        src_parts = src_path.lower()
        for candidate in candidates:
            if candidate.lower() in src_parts:
                comp_scores[comp_name] += 1
                break

    if comp_scores:
        return max(comp_scores, key=comp_scores.get)
    return None


def _deduplicate_components(components: list[dict]) -> list[dict]:
    """
    Merge components with the same name.

    The LLM can assign identical names to different Leiden clusters.
    When that happens, combine their files and take the higher confidence score.
    Names are stripped of whitespace before comparison to catch invisible
    differences from LLM output.
    """
    # First pass: normalize names (strip whitespace)
    for comp in components:
        comp["name"] = comp["name"].strip()

    merged: dict[str, dict] = {}
    for comp in components:
        name = comp["name"]
        if name in merged:
            existing = merged[name]
            # Merge files (deduplicate)
            seen = set(existing["files"])
            for f in comp["files"]:
                if f not in seen:
                    existing["files"].append(f)
                    seen.add(f)
            # Keep higher confidence
            existing["confidence"] = max(
                existing.get("confidence") or 0.0,
                comp.get("confidence") or 0.0,
            )
            # Combine purposes if different
            p1 = existing.get("purpose", "")
            p2 = comp.get("purpose", "")
            if p2 and p2 not in p1:
                existing["purpose"] = f"{p1}; {p2}".strip("; ")
        else:
            merged[name] = dict(comp)
            merged[name]["files"] = list(comp["files"])
    return list(merged.values())


def _write_to_db(
    conn: sqlite3.Connection,
    module_id: int,
    components: list[dict],
    test_assignments: dict[str, str],
    l3_edges: list[dict],
    run_id: int | None,
) -> None:
    """Write components, files, and edges to the database."""
    components = _deduplicate_components(components)

    # Build name -> db_id mapping for edge resolution
    name_to_id = {}

    for comp in components:
        comp_name = comp["name"]
        purpose = comp.get("purpose", "")
        confidence = comp.get("confidence")

        try:
            component_id = db.add_component(
                conn, module_id, comp_name, purpose, confidence, run_id
            )
        except sqlite3.IntegrityError:
            # Safety net: if deduplication missed a collision, reuse existing ID
            row = conn.execute(
                "SELECT id FROM components WHERE module_id = ? AND name = ?",
                (module_id, comp_name),
            ).fetchone()
            if row:
                component_id = row[0]
            else:
                raise
        name_to_id[comp_name] = component_id

        # Source files
        source_paths = comp["files"]
        is_test_flags = [False] * len(source_paths)
        db.add_component_files(conn, component_id, source_paths, is_test_flags)

    # Add test file assignments
    for test_path, comp_name in test_assignments.items():
        if comp_name == "unassigned":
            continue
        comp_id = name_to_id.get(comp_name)
        if comp_id:
            db.add_component_files(conn, comp_id, [test_path], [True])

    # Write L3 edges with name -> ID resolution
    for edge in l3_edges:
        source_id = name_to_id.get(edge["source"])
        target_id = name_to_id.get(edge["target"])
        if source_id and target_id:
            metadata = json.dumps(edge.get("metadata", {}))
            db.add_component_edge(
                conn, source_id, target_id,
                edge["edge_type"], edge["weight"],
                metadata, run_id,
            )
