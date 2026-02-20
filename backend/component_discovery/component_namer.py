# Doc: Natural_Language_Code/component_discovery/info_component_discovery.md
"""
Name clusters by their dominant leaf directory.

Strategy:
1. Group files by cluster ID
2. Name each cluster by most common leaf directory
3. Deduplicate collisions: prepend parent dir, then numeric suffix
"""

from collections import Counter, defaultdict
from pathlib import PurePosixPath


def name_components(clusters: dict[str, int]) -> dict[str, str]:
    """
    Name each component by its dominant directory.

    Args:
        clusters: dict mapping file_path -> cluster_id

    Returns:
        dict mapping file_path -> component_name
    """
    if not clusters:
        return {}

    cluster_files = defaultdict(list)
    for file_path, cluster_id in clusters.items():
        cluster_files[cluster_id].append(file_path)

    cluster_names = {}
    for cluster_id, files in cluster_files.items():
        dirs = [PurePosixPath(f).parent.name for f in files]
        dirs = [d for d in dirs if d] or ["root"]
        dominant_dir = Counter(dirs).most_common(1)[0][0]
        cluster_names[cluster_id] = dominant_dir

    cluster_names = _deduplicate_names(cluster_names, cluster_files)

    return {
        file_path: cluster_names[cluster_id]
        for file_path, cluster_id in clusters.items()
    }


def _deduplicate_names(
    cluster_names: dict[int, str],
    cluster_files: dict[int, list[str]],
) -> dict[int, str]:
    """
    Make component names unique.

    Strategy 1: prepend parent directory for duplicates
    Strategy 2: numeric suffix if still colliding
    """
    name_counts = Counter(cluster_names.values())
    duplicates = {name for name, count in name_counts.items() if count > 1}

    if not duplicates:
        return cluster_names

    result = {}
    for cluster_id, name in cluster_names.items():
        if name in duplicates:
            files = cluster_files[cluster_id]
            parents = [PurePosixPath(f).parent.parent.name for f in files]
            parents = [p for p in parents if p] or ["root"]
            common_parent = Counter(parents).most_common(1)[0][0]
            result[cluster_id] = f"{common_parent}/{name}"
        else:
            result[cluster_id] = name

    final_name_counts = Counter(result.values())
    still_duped = {name for name, count in final_name_counts.items() if count > 1}

    if still_duped:
        seen = defaultdict(int)
        final = {}
        for cluster_id in sorted(result.keys()):
            name = result[cluster_id]
            if name in still_duped:
                seen[name] += 1
                final[cluster_id] = f"{name}_{seen[name]}"
            else:
                final[cluster_id] = name
        return final

    return result
