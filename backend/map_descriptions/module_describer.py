# Doc: Natural_Language_Code/map_descriptions/info_map_descriptions_module.md
"""
Phase B — Module Descriptions.

For each module:
1. Elevation: collect component decisions, identify cross-cutting patterns, elevate to module level
2. Deployment: scan for deployment files, extract deployment-level decisions

Parallelism strategy:
- Pre-read all component decisions and deployment files from DB/disk (main thread, serial).
- Run module workers in parallel (MAX_WORKERS concurrent).
  Within each module worker, elevation + deployment LLM calls run concurrently.
  Workers have no DB access — safe to run in any thread.
- Write all results to DB serially (main thread — no SQLite contention).
"""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import NamedTuple

import db
from map_descriptions.llm_client import LLMClient
from map_descriptions.prompts import (
    MODULE_ELEVATION_SYSTEM, elevation_prompt,
    MODULE_DEPLOYMENT_SYSTEM, deployment_prompt,
)

MAX_WORKERS = 5  # concurrent module workers

_DEPLOYMENT_FILENAMES = [
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "package.json",
    "requirements.txt",
    "pyproject.toml",
    "Makefile",
    ".env.example",
    "Procfile",
    "vercel.json",
    "fly.toml",
]

_DEPLOYMENT_SUBDIRS = ["config", "k8s", "helm"]


class _ModuleResult(NamedTuple):
    module_id: int
    module_name: str
    elevated: list[dict]   # [{text, ids_to_delete}]
    deployment: list[str]  # [text, ...]


def describe_all_modules(
    conn: sqlite3.Connection,
    source_dir: str,
    client: LLMClient,
    run_id: int,
) -> None:
    """
    Generate module-level decisions for all eligible modules using parallel LLM calls.

    1. Pre-read component decisions + module dirs from DB, deployment files from disk (serial).
    2. Run module workers in parallel (MAX_WORKERS concurrent).
       Each worker runs elevation + deployment LLM calls concurrently.
    3. Write results to DB serially.
    """
    # Step 1: Pre-read all data from DB/disk before spawning threads
    eligible = [
        m for m in db.get_modules(conn)
        if m.get("classification") != "supporting-asset"
        and m.get("source_origin") == "in-repo"
    ]

    if not eligible:
        print("  [Part 3] Phase B: no eligible modules found")
        return

    module_inputs: list[tuple[dict, dict, dict[str, str]]] = []
    for module in eligible:
        component_decisions = _collect_component_decisions(conn, module["id"])
        module_dirs = db.get_module_directories(conn, module["id"])
        deployment_files = _find_deployment_files(source_dir, module_dirs)
        module_inputs.append((module, component_decisions, deployment_files))

    print(f"  [Part 3] Phase B: {len(module_inputs)} modules ({MAX_WORKERS} parallel)...")

    # Step 2: LLM calls in parallel — workers do NOT touch the DB
    results: list[_ModuleResult] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_module = {
            executor.submit(
                _describe_module_worker, module, component_decisions, deployment_files, client
            ): module
            for module, component_decisions, deployment_files in module_inputs
        }
        for future in as_completed(future_to_module):
            module = future_to_module[future]
            try:
                result = future.result()
                results.append(result)
                print(
                    f"  [Part 3]   {module['name']}: "
                    f"{len(result.elevated)} cross-cutting, {len(result.deployment)} deployment"
                )
            except Exception as e:
                print(f"  [Part 3]   Warning: module {module['name']} failed: {e}")

    # Step 3: Write to DB serially
    total_elevated = total_deployment = 0
    for result in results:
        for item in result.elevated:
            db.add_decision(conn, "cross_cutting", item["text"], module_id=result.module_id, run_id=run_id)
            if item.get("ids_to_delete"):
                db.delete_decisions_by_ids(conn, item["ids_to_delete"])
            total_elevated += 1
        for text in result.deployment:
            db.add_decision(conn, "deployment", text, module_id=result.module_id, run_id=run_id)
            total_deployment += 1

    print(
        f"  [Part 3] Phase B complete: "
        f"{total_elevated} cross-cutting, {total_deployment} deployment decisions written"
    )


def describe_module(
    conn: sqlite3.Connection,
    module: dict,
    source_dir: str,
    client: LLMClient,
    run_id: int,
) -> None:
    """Single-module convenience wrapper. Pre-reads from DB, calls worker, writes to DB."""
    component_decisions = _collect_component_decisions(conn, module["id"])
    module_dirs = db.get_module_directories(conn, module["id"])
    deployment_files = _find_deployment_files(source_dir, module_dirs)

    result = _describe_module_worker(module, component_decisions, deployment_files, client)

    for item in result.elevated:
        db.add_decision(conn, "cross_cutting", item["text"], module_id=result.module_id, run_id=run_id)
        if item.get("ids_to_delete"):
            db.delete_decisions_by_ids(conn, item["ids_to_delete"])
    for text in result.deployment:
        db.add_decision(conn, "deployment", text, module_id=result.module_id, run_id=run_id)

    print(
        f"  [Part 3]   {module['name']}: "
        f"{len(result.elevated)} cross-cutting, {len(result.deployment)} deployment decisions written"
    )


# ---------------------------------------------------------------------------
# Worker — no DB access, safe to call from any thread
# ---------------------------------------------------------------------------

def _describe_module_worker(
    module: dict,
    component_decisions: dict[str, list[dict]],
    deployment_files: dict[str, str],
    client: LLMClient,
) -> _ModuleResult:
    """
    Run elevation + deployment LLM calls for one module.
    Both calls run concurrently (they have independent inputs).
    Returns a _ModuleResult with all data ready to write to DB.
    """
    module_id = module["id"]
    module_name = module["name"]
    module_type = module.get("type") or "module"
    module_technology = module.get("technology") or "unknown"
    module_deployment_target = module.get("deployment_target") or "unknown"

    elevated_items: list[dict] = []
    deployment_texts: list[str] = []

    # Determine which LLM calls to make
    run_elevation = len(component_decisions) > 1
    run_deployment = bool(deployment_files)

    if not run_elevation and not run_deployment:
        return _ModuleResult(module_id, module_name, [], [])

    with ThreadPoolExecutor(max_workers=2) as inner:
        futures: dict[str, object] = {}

        if run_elevation:
            decisions_text = _format_component_decisions(component_decisions)
            futures["elevation"] = inner.submit(
                _run_elevation_llm,
                module_name, module_type, module_technology, decisions_text, client,
            )

        if run_deployment:
            file_contents = _format_deployment_files(deployment_files)
            futures["deployment"] = inner.submit(
                _run_deployment_llm,
                module_name, module_type, module_technology,
                module_deployment_target, file_contents, client,
            )

        for task_name, future in futures.items():
            try:
                if task_name == "elevation":
                    elevated_items = future.result()
                else:
                    deployment_texts = future.result()
            except Exception as e:
                print(f"  [Part 3]   Warning: {task_name} failed for {module_name}: {e}")

    return _ModuleResult(module_id, module_name, elevated_items, deployment_texts)


def _run_elevation_llm(
    module_name: str,
    module_type: str,
    module_technology: str,
    decisions_text: str,
    client: LLMClient,
) -> list[dict]:
    """Call LLM for elevation. Returns [{text, ids_to_delete}]."""
    prompt = elevation_prompt(module_name, module_type, module_technology, decisions_text)
    response = client.query(prompt, system=MODULE_ELEVATION_SYSTEM, max_tokens=4096)
    result = []
    for item in response.get("elevated", []):
        text = item.get("text", "").strip()
        if not text:
            continue
        ids_raw = item.get("source_decision_ids", [])
        result.append({
            "text": text,
            "ids_to_delete": [int(i) for i in ids_raw] if ids_raw else [],
        })
    return result


def _run_deployment_llm(
    module_name: str,
    module_type: str,
    module_technology: str,
    deployment_target: str,
    file_contents: str,
    client: LLMClient,
) -> list[str]:
    """Call LLM for deployment decisions. Returns [text, ...]."""
    prompt = deployment_prompt(
        module_name, module_type, module_technology, deployment_target, file_contents
    )
    response = client.query(prompt, system=MODULE_DEPLOYMENT_SYSTEM, max_tokens=2048)
    return [
        d["text"].strip()
        for d in response.get("decisions", [])
        if d.get("text", "").strip()
    ]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _collect_component_decisions(
    conn: sqlite3.Connection,
    module_id: int,
) -> dict[str, list[dict]]:
    """Return {component_name: [{id, category, text}, ...]} for all components in a module."""
    components = db.get_components(conn, module_id)
    result: dict[str, list[dict]] = {}
    for comp in components:
        decisions = db.get_decisions(conn, component_id=comp["id"])
        if decisions:
            result[comp["name"]] = [
                {"id": d["id"], "category": d["category"], "text": d["text"]}
                for d in decisions
            ]
    return result


def _find_deployment_files(source_dir: str, module_dirs: list[str]) -> dict[str, str]:
    """Scan module directories (and repo root) for deployment-related files."""
    source = Path(source_dir)

    search_dirs: list[Path] = [source]
    for d in module_dirs:
        candidate = source / d
        if candidate not in search_dirs:
            search_dirs.append(candidate)

    result: dict[str, str] = {}

    for search_dir in search_dirs:
        if not search_dir.is_dir():
            continue

        for filename in _DEPLOYMENT_FILENAMES:
            candidate = search_dir / filename
            if candidate.exists() and candidate.is_file():
                rel_path = str(candidate.relative_to(source))
                if rel_path not in result:
                    content = _read_deployment_file(candidate, filename)
                    if content:
                        result[rel_path] = content

        for subdir_name in _DEPLOYMENT_SUBDIRS:
            subdir = search_dir / subdir_name
            if not subdir.is_dir():
                continue
            for yml_file in sorted(subdir.glob("*.yml")) + sorted(subdir.glob("*.yaml")):
                rel_path = str(yml_file.relative_to(source))
                if rel_path not in result:
                    try:
                        result[rel_path] = yml_file.read_text(encoding="utf-8", errors="replace")[:5000]
                    except Exception:
                        pass

    return result


def _read_deployment_file(path: Path, filename: str) -> str:
    """Read a deployment file. Selectively parses package.json."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        if filename == "package.json":
            return _extract_package_json(raw)
        return raw[:8000]
    except Exception:
        return ""


def _extract_package_json(raw: str) -> str:
    """Extract only keys relevant to deployment from package.json."""
    try:
        data = json.loads(raw)
        selective = {}
        for key in ("scripts", "dependencies", "devDependencies", "engines", "main", "type"):
            if key in data:
                selective[key] = data[key]
        return json.dumps(selective, indent=2)
    except Exception:
        return raw[:4000]


def _format_component_decisions(component_decisions: dict[str, list[dict]]) -> str:
    parts = []
    for comp_name, decisions in component_decisions.items():
        lines = [f"Component: {comp_name}"]
        for d in decisions:
            lines.append(f"  [{d['id']}] ({d['category']}) {d['text']}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _format_deployment_files(files: dict[str, str]) -> str:
    parts = []
    for path, content in files.items():
        parts.append(f"--- file: {path} ---\n{content}")
    return "\n\n".join(parts)
