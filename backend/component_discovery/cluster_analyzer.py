# Doc: Natural_Language_Code/component_discovery/info_component_discovery.md
"""
LLM analysis per Leiden cluster.

For each cluster: assess cohesion, split/merge decisions, semantic naming,
misplaced file identification, confidence scoring.

On LLM failure, degrades to Leiden-only naming with confidence=0.3.
"""
 
from collections import defaultdict

from component_discovery.llm_client import LLMClient, estimate_tokens
from component_discovery.prompts import SYSTEM_PROMPT, cluster_analysis_prompt


def analyze_clusters(
    clusters: dict[int, list[str]],
    files_metadata: dict[str, dict],
    inter_file_edges: dict,
    client: LLMClient,
    log_fn: callable = print,
) -> tuple[list[dict], list[dict]]:
    """
    Analyze all Leiden clusters using LLM.

    Args:
        clusters: dict mapping cluster_id -> list of file paths
        files_metadata: per-file metadata from metadata_extractor
        inter_file_edges: {(file_a, file_b): count} call edges
        client: LLMClient instance

    Returns:
        List of component dicts:
            name, purpose, files, confidence, misplaced_files
    """
    all_components = []
    all_misplaced = []

    for cluster_id, file_paths in clusters.items():
        cluster_files = []
        for path in file_paths:
            if path in files_metadata:
                cluster_files.append(files_metadata[path])
            else:
                cluster_files.append({
                    "path": path,
                    "docstring": "",
                    "exports": [],
                    "symbol_kinds": {},
                })

        dir_breakdown = defaultdict(int)
        for f in cluster_files:
            dir_breakdown[f.get("directory", "")] += 1

        cluster_file_set = set(file_paths)
        top_edges = []
        for (a, b), count in inter_file_edges.items():
            if a in cluster_file_set and b in cluster_file_set and a < b:
                top_edges.append((a, b, count))
        top_edges.sort(key=lambda x: -x[2])
        top_edges = top_edges[:10]

        # Use cluster_id as name for prompt
        cluster_name = f"cluster_{cluster_id}"

        prompt = cluster_analysis_prompt(
            cluster_name=cluster_name,
            files=cluster_files,
            top_edges=top_edges,
            directory_breakdown=dict(dir_breakdown),
        )

        # Truncate if too large (cap at 30 files per NLC doc)
        if estimate_tokens(prompt) > 8000:
            cluster_files = cluster_files[:30]
            prompt = cluster_analysis_prompt(
                cluster_name=cluster_name,
                files=cluster_files,
                top_edges=top_edges,
                directory_breakdown=dict(dir_breakdown),
            )

        try:
            result = client.query(prompt, system=SYSTEM_PROMPT)

            if result.get("should_split") and len(result.get("components", [])) > 1:
                sub_components = result["components"]
                log_fn(f"  [LLM] {cluster_name}: split into {len(sub_components)} components")
                # Validate LLM file paths against original SCIP-derived cluster files.
                # LLM controls naming and split decisions only — file paths come from SCIP.
                original_file_set = set(file_paths)
                assigned_files: set[str] = set()
                validated_sub = []
                for comp in sub_components:
                    valid = [f for f in comp.get("files", []) if f in original_file_set]
                    assigned_files.update(valid)
                    validated_sub.append({
                        "name": comp["name"],
                        "purpose": comp.get("purpose", ""),
                        "files": valid,
                        "confidence": result.get("confidence", 0.7),
                    })
                # Any files the LLM didn't assign go to the first sub-component
                unassigned = [f for f in file_paths if f not in assigned_files]
                if unassigned and validated_sub:
                    validated_sub[0]["files"].extend(unassigned)
                for comp in validated_sub:
                    if comp["files"]:
                        all_components.append(comp)
            else:
                components = result.get("components", [])
                if components:
                    comp = components[0]
                    named = comp.get("name", cluster_name)
                    confidence = result.get("confidence", 0.7)
                    log_fn(f"  [LLM] {cluster_name} -> '{named}' (confidence={confidence:.2f})")
                    all_components.append({
                        "name": named,
                        "purpose": comp.get("purpose", result.get("primary_responsibility", "")),
                        "files": file_paths,  # Always use SCIP-derived Leiden files, not LLM list
                        "confidence": confidence,
                    })
                else:
                    all_components.append({
                        "name": cluster_name,
                        "purpose": result.get("primary_responsibility", ""),
                        "files": file_paths,
                        "confidence": result.get("confidence", 0.5),
                    })

            # Collect misplaced files for cross-cluster reconciliation
            misplaced_count = len(result.get("misplaced_files", []))
            if misplaced_count:
                log_fn(f"  [LLM] {cluster_name}: {misplaced_count} misplaced file(s) flagged")
            for mf in result.get("misplaced_files", []):
                all_misplaced.append({
                    "path": mf["path"],
                    "suggested_home": mf.get("suggested_home", ""),
                    "from_cluster": cluster_id,
                    "confidence": result.get("confidence", 0.7),
                })

        except Exception as e:
            # Degrade gracefully: Leiden-only naming, confidence=0.3
            log_fn(f"  [LLM] Warning: analysis failed for {cluster_name} ({len(file_paths)} files): {e}")
            all_components.append({
                "name": cluster_name,
                "purpose": "",
                "files": file_paths,
                "confidence": 0.3,
            })

    return all_components, all_misplaced


def reconcile_misplaced_files(
    components: list[dict],
    misplaced: list[dict],
) -> list[dict]:
    """
    Process misplaced file suggestions across all clusters.

    Move files to their suggested component if it exists.
    Resolve conflicts by assigning to the higher-confidence cluster.
    """
    if not misplaced:
        return components

    # Build name -> component index
    comp_by_name = {}
    for i, comp in enumerate(components):
        comp_by_name[comp["name"]] = i

    # Group moves by file path to detect conflicts
    moves_by_file = defaultdict(list)
    for mf in misplaced:
        moves_by_file[mf["path"]].append(mf)

    for file_path, suggestions in moves_by_file.items():
        # Pick highest confidence suggestion
        best = max(suggestions, key=lambda s: s.get("confidence", 0))
        target_name = best["suggested_home"]

        if target_name not in comp_by_name:
            continue

        target_idx = comp_by_name[target_name]

        # Remove from current component(s)
        for comp in components:
            if file_path in comp["files"]:
                comp["files"].remove(file_path)

        # Add to target
        if file_path not in components[target_idx]["files"]:
            components[target_idx]["files"].append(file_path)

    # Remove empty components
    components = [c for c in components if c["files"]]

    return components
