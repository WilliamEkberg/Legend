# Doc: Natural_Language_Code/component_discovery/info_component_discovery.md
"""
LLM prompt templates for component discovery.

Only cluster_analysis_prompt — no module_discovery_prompt (L2 is handled by Part 1).
"""


SYSTEM_PROMPT = """You are a software architect analyzing a codebase to extract its architectural structure.

You will be given information about files in a cluster (a group of related files identified by the Leiden algorithm) and asked to:
1. Determine if the cluster represents a single cohesive component or should be split
2. Name and describe the component(s)
3. Identify any misplaced files

Always respond with valid JSON matching the requested schema. Be concise but precise in descriptions."""


def cluster_analysis_prompt(
    cluster_name: str,
    files: list[dict],
    top_edges: list[tuple],
    directory_breakdown: dict,
) -> str:
    """
    Generate prompt for analyzing a single Leiden cluster.

    Args:
        cluster_name: Name of the Leiden cluster
        files: List of file metadata dicts (path, docstring, exports, symbol_kinds)
        top_edges: Top inter-file edges [(file_a, file_b, count), ...]
        directory_breakdown: Dict of directory -> file count

    Returns:
        Prompt string
    """
    file_summaries = []
    for f in files:
        summary = f"- **{f['path']}**"
        if f.get("exports"):
            summary += f" exports: {', '.join(f['exports'][:5])}"
        if f.get("docstring"):
            doc = f["docstring"][:150].replace("\n", " ")
            summary += f'\n  "{doc}"'
        file_summaries.append(summary)

    file_list = "\n".join(file_summaries)

    edge_list = "\n".join(
        f"  {a} <-> {b} ({count} calls)"
        for a, b, count in top_edges[:10]
    )

    dir_list = "\n".join(
        f"  {d}: {count} files"
        for d, count in sorted(directory_breakdown.items(), key=lambda x: -x[1])[:10]
    )

    return f"""Analyze this code cluster from Leiden clustering.

## Cluster: {cluster_name}
Total files: {len(files)}

## Directory breakdown:
{dir_list}

## Files:
{file_list}

## Top inter-file dependencies (call counts):
{edge_list if edge_list else "  (no significant internal dependencies)"}

---

Analyze this cluster and respond with JSON:

```json
{{
  "cluster_id": "{cluster_name}",
  "primary_responsibility": "1-2 sentence description of what this code does",
  "cohesion": "high" | "medium" | "low",
  "should_split": true | false,
  "components": [
    {{
      "name": "component_name_snake_case",
      "purpose": "1 sentence purpose",
      "files": ["path/to/file1.py", "path/to/file2.py"]
    }}
  ],
  "misplaced_files": [
    {{
      "path": "file/that/doesnt/belong.py",
      "reason": "why it seems misplaced",
      "suggested_home": "component or cluster it should belong to"
    }}
  ],
  "confidence": 0.0-1.0
}}
```

Guidelines:
- If cohesion is high, keep as ONE component (components array has 1 item)
- If cohesion is low, SPLIT into 2-4 logical components
- Component names should be domain-specific (e.g., "http_request_handler" not "utils_1")
- Only flag misplaced_files if clearly wrong (e.g., test file in non-test cluster)
- Confidence: 0.9+ if clear, 0.6-0.8 if reasonable guess, <0.6 if uncertain"""
