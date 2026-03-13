# Doc: Natural_Language_Code/component_discovery/info_component_discovery.md
"""
Directory-based hierarchical pre-partitioning for Leiden clustering.
"""

from collections import defaultdict
from pathlib import PurePosixPath


def partition_files(
    source_files: set[str],
    min_partition_size: int = 5,
    depth: int = 2,
    target_components: int | None = None,
) -> list[set[str]]:
    """Partition files by depth-N directory prefix into independently-clustered groups."""
    if not source_files:
        return []

    groups = defaultdict(set)
    for path in source_files:
        parts = PurePosixPath(path).parts
            if len(parts) > depth:
            prefix = "/".join(parts[:depth])
        elif len(parts) > 1:
            prefix = "/".join(parts[:-1])
        else:
            prefix = "."
        groups[prefix].add(path)

    partitions = []
    remainder = set()

    for prefix, files in groups.items():
        if len(files) >= min_partition_size:
            partitions.append(files)
        else:
            remainder.update(files)

    if remainder:
        if partitions and len(remainder) < min_partition_size:
            # Merge tiny remainder into the smallest partition
            smallest = min(partitions, key=len)
            smallest.update(remainder)
        else:
            partitions.append(remainder)

    if not partitions:
        return [source_files]

    if target_components is not None and len(partitions) * 2 > target_components * 3:
        return [source_files]

    return partitions
