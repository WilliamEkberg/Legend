# Doc: Natural_Language_Code/revalidation/info_revalidation.md
"""
New File Classification — Phase 0.

Detects source files added since the last pipeline run and classifies them
into existing (or new) components using an LLM.

Two stages:
1. Detection (no LLM): walk module directories on disk, diff against
   component_files table to find untracked files.
2. Classification (LLM): for each module with new files, ask the LLM
   which component each file belongs to (or propose a new component).
"""

import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import db
from component_discovery.test_filter import is_test_file
from map_descriptions.component_describer import (
    BINARY_EXTENSIONS,
    _read_component_source,
    _format_file_contents,
)
from map_descriptions.llm_client import LLMClient
from revalidation.prompts import (
    NEW_FILE_CLASSIFICATION_SYSTEM,
    new_file_classification_prompt,
)

MAX_WORKERS = 5

# Non-source files to skip during disk walk. BINARY_EXTENSIONS covers binaries;
# this covers text-based files that are not source code and would never appear
# in the SCIP index / component_files table.
SKIP_EXTENSIONS = BINARY_EXTENSIONS | {
    # Documentation / text
    ".rst", ".adoc",
    # Data / config
    ".json", ".yaml", ".yml", ".toml", ".xml", ".csv", ".tsv",
    ".ini", ".cfg", ".conf", ".properties",
    # Lock files / dependency manifests
    ".lock", ".sum",
    # Environment / secrets
    ".env", ".envrc",
    # Build / CI artifacts
    ".log", ".out", ".pid",
    # Markup / templates (non-code)
    ".html", ".htm", ".css", ".scss", ".sass", ".less",
    ".svg", ".map",
    # Shell config / dotfiles
    ".gitignore", ".gitattributes", ".editorconfig",
    ".dockerignore", ".prettierrc", ".eslintrc",
}


def classify_new_files(
    conn: sqlite3.Connection,
    source_dir: str,
    client: LLMClient,
) -> dict:
    """
    Detect untracked files and classify them into components.

    Returns summary: {new_files_found, files_assigned, components_created, orphan_files, new_file_paths}.
    """
    stats = {"new_files_found": 0, "files_assigned": 0, "components_created": 0, "orphan_files": 0, "new_file_paths": []}

    # ------------------------------------------------------------------
    # Stage 1: Detection (no LLM)
    # ------------------------------------------------------------------

    # Collect all files currently on disk within module directories
    modules = db.get_modules(conn)
    eligible_modules = [
        m for m in modules
        if m.get("classification") != "supporting-asset"
        and m.get("source_origin") == "in-repo"
    ]

    if not eligible_modules:
        print("  [Phase 0] No eligible modules found")
        return stats

    # Build module_id → (module, directories) mapping
    module_dirs: dict[int, tuple[dict, list[str]]] = {}
    for mod in eligible_modules:
        dirs = db.get_module_directories(conn, mod["id"])
        if dirs:
            module_dirs[mod["id"]] = (mod, dirs)

    # Walk disk to find all source files
    source_root = Path(source_dir)
    disk_files: dict[int, list[str]] = {}  # module_id → [relative paths]

    for mod_id, (mod, dirs) in module_dirs.items():
        for dir_path in dirs:
            abs_dir = source_root / dir_path
            if not abs_dir.is_dir():
                continue
            for root, _dirnames, filenames in os.walk(abs_dir):
                for fname in filenames:
                    if fname.startswith("."):
                        continue
                    abs_file = Path(root) / fname
                    ext = abs_file.suffix.lower()
                    if not ext or ext in SKIP_EXTENSIONS:
                        continue
                    rel_path = str(abs_file.relative_to(source_root))
                    disk_files.setdefault(mod_id, []).append(rel_path)

    # Get all tracked files from DB
    tracked_files = db.get_all_component_file_paths(conn)

    # Diff: new files = on disk but not tracked
    new_files_by_module: dict[int, list[str]] = {}
    for mod_id, paths in disk_files.items():
        new_paths = [p for p in paths if p not in tracked_files]
        if new_paths:
            new_files_by_module[mod_id] = new_paths

    total_new = sum(len(v) for v in new_files_by_module.values())
    stats["new_files_found"] = total_new

    if total_new == 0:
        print("  [Phase 0] No new files detected")
        return stats

    print(f"  [Phase 0] Detected {total_new} new files across {len(new_files_by_module)} modules")

    # ------------------------------------------------------------------
    # Stage 2: Classification (one LLM call per file, parallel)
    # ------------------------------------------------------------------

    # Pre-read module context: existing components with their files
    # work_item = (module, comp_info, file_path)
    file_work_items: list[tuple[dict, list[dict], str]] = []
    test_files_by_module: dict[int, list[str]] = {}  # module_id → test paths

    for mod_id, new_paths in new_files_by_module.items():
        mod, _dirs = module_dirs[mod_id]
        components = db.get_components(conn, mod_id)
        comp_info = []
        for comp in components:
            comp_files = db.get_component_files(conn, comp["id"], exclude_test=True)
            comp_info.append({
                "id": comp["id"],
                "name": comp["name"],
                "purpose": comp.get("purpose"),
                "files": comp_files,
            })

        for path in new_paths:
            if is_test_file(path):
                test_files_by_module.setdefault(mod_id, []).append(path)
            else:
                file_work_items.append((mod, comp_info, path))

    if not file_work_items:
        print("  [Phase 0] No new source files to classify (only test files)")
    else:
        print(f"  [Phase 0] Classifying {len(file_work_items)} source files ({MAX_WORKERS} parallel)...")

    # Parallel LLM calls — one per file
    # result = (module, comp_info, classification_dict)
    file_results: list[tuple[dict, list[dict], dict]] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_item = {
            executor.submit(
                _classify_single_file_worker,
                mod, comp_info, file_path, source_dir, client,
            ): (mod, comp_info, file_path)
            for mod, comp_info, file_path in file_work_items
        }
        for future in as_completed(future_to_item):
            mod, comp_info, file_path = future_to_item[future]
            try:
                classification = future.result()
                if classification:
                    file_results.append((mod, comp_info, classification))
                    target = classification.get("existing_component") or classification.get("new_component", {}).get("name", "?")
                    print(f"  [Phase 0]   {file_path} → {target}")
            except Exception as e:
                print(f"  [Phase 0]   Warning: {file_path} classification failed: {e}")

    # ------------------------------------------------------------------
    # Stage 3: Write to DB
    # ------------------------------------------------------------------

    # Group by module for new-component dedup and test file assignment
    name_to_id_by_module: dict[int, dict[str, int]] = {}
    comp_info_by_module: dict[int, list[dict]] = {}
    source_assignments: dict[str, int] = {}  # file_path → component_id

    for mod, comp_info, cls in file_results:
        mod_id = mod["id"]

        # Lazily build name→id lookup per module
        if mod_id not in name_to_id_by_module:
            name_to_id_by_module[mod_id] = {c["name"]: c["id"] for c in comp_info}
            comp_info_by_module[mod_id] = comp_info
        name_to_id = name_to_id_by_module[mod_id]

        file_path = cls.get("file", "")
        existing = cls.get("existing_component")
        new_comp = cls.get("new_component")

        if existing and existing in name_to_id:
            comp_id = name_to_id[existing]
            db.add_component_files(conn, comp_id, [file_path], [False])
            source_assignments[file_path] = comp_id
            stats["files_assigned"] += 1
            stats["new_file_paths"].append(file_path)

        elif new_comp:
            comp_name = new_comp.get("name", "")
            comp_purpose = new_comp.get("purpose", "")

            if comp_name in name_to_id:
                # Already exists (either pre-existing or created earlier in this batch)
                comp_id = name_to_id[comp_name]
            else:
                comp_id = db.add_component(
                    conn, mod_id, comp_name, comp_purpose, confidence=0.5,
                )
                name_to_id[comp_name] = comp_id
                stats["components_created"] += 1

            db.add_component_files(conn, comp_id, [file_path], [False])
            source_assignments[file_path] = comp_id
            stats["files_assigned"] += 1
            stats["new_file_paths"].append(file_path)

    # Assign test files using path-similarity fallback
    for mod_id, test_paths in test_files_by_module.items():
        name_to_id = name_to_id_by_module.get(mod_id, {})
        comp_info = comp_info_by_module.get(mod_id, [])
        for test_path in test_paths:
            assigned_comp_id = _match_test_to_component(
                test_path, source_assignments, name_to_id, comp_info,
            )
            if assigned_comp_id:
                db.add_component_files(conn, assigned_comp_id, [test_path], [True])
                stats["files_assigned"] += 1
                stats["new_file_paths"].append(test_path)

    print(
        f"  [Phase 0] Done. "
        f"new_files={stats['new_files_found']}, "
        f"assigned={stats['files_assigned']}, "
        f"components_created={stats['components_created']}"
    )
    return stats


# ---------------------------------------------------------------------------
# Worker — no DB access, safe to call from any thread
# ---------------------------------------------------------------------------

def _classify_single_file_worker(
    module: dict,
    existing_components: list[dict],
    file_path: str,
    source_dir: str,
    client: LLMClient,
) -> dict | None:
    """
    Classify a single new file via LLM.
    Returns {file, existing_component} or {file, new_component: {name, purpose}}, or None.
    """
    files = _read_component_source(source_dir, [file_path])
    if not files:
        return None

    file_contents = _format_file_contents(files)

    prompt = new_file_classification_prompt(
        module["name"], existing_components, file_contents,
    )
    response = client.query(prompt, system=NEW_FILE_CLASSIFICATION_SYSTEM, max_tokens=1024)

    classifications = response.get("classifications", [])
    return classifications[0] if classifications else None


# ---------------------------------------------------------------------------
# Test file assignment helper
# ---------------------------------------------------------------------------

def _match_test_to_component(
    test_path: str,
    source_assignments: dict[str, int],
    name_to_id: dict[str, int],
    comp_info: list[dict],
) -> int | None:
    """
    Try to assign a test file to a component using path similarity.

    Fallback chain:
    1. If a source file in the same directory was assigned, use that component.
    2. If the test file's directory name matches a component name, use that.
    """
    test_dir = str(Path(test_path).parent)

    # Check if any source file in the same or parent directory was assigned
    for src_path, comp_id in source_assignments.items():
        src_dir = str(Path(src_path).parent)
        if test_dir == src_dir or test_dir.startswith(src_dir + "/"):
            return comp_id

    # Check directory name match against component names
    dir_name = Path(test_path).parent.name.lower()
    for comp in comp_info:
        if comp["name"].lower().replace("_", "").replace("-", "") == dir_name.replace("_", "").replace("-", ""):
            return comp["id"]

    return None
