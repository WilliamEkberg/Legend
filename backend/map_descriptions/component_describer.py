# Doc: Natural_Language_Code/map_descriptions/info_map_descriptions_component.md
"""
Phase A — Component Descriptions.

For each component: read source files -> LLM extraction -> validate -> write decisions to DB.

Parallelism strategy:
1. Pre-read all component file paths from DB (main thread, serial).
2. Fire all LLM calls concurrently via ThreadPoolExecutor (MAX_WORKERS).
   Workers have no DB access — safe to run in any thread.
3. Collect (component_id, decisions) results as futures complete.
4. Write all decisions to DB serially (main thread — no SQLite contention).
"""

import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import db
from map_descriptions.llm_client import LLMClient
from map_descriptions.prompts import COMPONENT_SYSTEM, component_extraction_prompt

VALID_CATEGORIES = {
    "api_contracts",
    "patterns",
    "libraries",
    "boundaries",
    "error_handling",
    "data_flow",
}

TOKEN_BUDGET = 150_000
MAX_FILE_LINES = 1500
MAX_WORKERS = 5  # concurrent LLM calls

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".woff", ".woff2",
    ".ttf", ".eot", ".otf", ".mp3", ".mp4", ".wav", ".pdf", ".zip",
    ".tar", ".gz", ".db", ".sqlite", ".pyc", ".pyo", ".so", ".dll",
    ".exe", ".bin", ".class", ".jar", ".wasm",
}


def describe_all_components(
    conn: sqlite3.Connection,
    source_dir: str,
    client: LLMClient,
    run_id: int,
) -> None:
    """
    Extract decisions for all eligible components using parallel LLM calls.

    1. Pre-read all file paths from DB (serial).
    2. Run LLM calls in parallel (MAX_WORKERS concurrent).
    3. Write results to DB serially.
    """
    # Step 1: Build work list — read everything from DB before spawning threads
    work_items: list[tuple[dict, list[str]]] = []
    for module in db.get_modules(conn):
        if module.get("classification") == "supporting-asset":
            print(f"  [Part 3] Skipping module {module['name']}: supporting-asset")
            continue
        if module.get("source_origin") != "in-repo":
            print(f"  [Part 3] Skipping module {module['name']}: source_origin={module.get('source_origin')}")
            continue
        for component in db.get_components(conn, module["id"]):
            file_paths = db.get_component_files(conn, component["id"], exclude_test=True)
            if file_paths:
                work_items.append((component, file_paths))
            else:
                print(f"  [Part 3]   {component['name']}: no source files, skipping")

    if not work_items:
        print("  [Part 3] Phase A: no eligible components found")
        return

    print(f"  [Part 3] Phase A: {len(work_items)} components ({MAX_WORKERS} parallel)...")

    # Step 2: LLM calls in parallel — workers do NOT touch the DB
    results: list[tuple[int, list[dict]]] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_comp = {
            executor.submit(_describe_component_worker, component, file_paths, source_dir, client): component
            for component, file_paths in work_items
        }
        for future in as_completed(future_to_comp):
            component = future_to_comp[future]
            try:
                component_id, decisions = future.result()
                results.append((component_id, decisions))
                print(f"  [Part 3]   {component['name']}: {len(decisions)} decisions")
            except Exception as e:
                print(f"  [Part 3]   Warning: {component['name']} failed: {e}")

    # Step 3: Write to DB serially
    total = 0
    for component_id, decisions in results:
        for d in decisions:
            db.add_decision(
                conn, d["category"], d["text"],
                component_id=component_id, run_id=run_id,
                detail=d.get("detail"),
            )
            total += 1

    print(f"  [Part 3] Phase A complete: {len(results)} components, {total} decisions written")


def describe_component(
    conn: sqlite3.Connection,
    component: dict,
    source_dir: str,
    client: LLMClient,
    run_id: int,
) -> None:
    """Single-component convenience wrapper. Reads file paths, calls worker, writes to DB."""
    file_paths = db.get_component_files(conn, component["id"], exclude_test=True)
    if not file_paths:
        print(f"  [Part 3]   {component['name']}: no source files, skipping")
        return

    component_id, decisions = _describe_component_worker(component, file_paths, source_dir, client)
    for d in decisions:
        db.add_decision(conn, d["category"], d["text"], component_id=component_id, run_id=run_id, detail=d.get("detail"))
    print(f"  [Part 3]   {component['name']}: {len(decisions)} decisions written")


# ---------------------------------------------------------------------------
# Worker — no DB access, safe to call from any thread
# ---------------------------------------------------------------------------

def _describe_component_worker(
    component: dict,
    file_paths: list[str],
    source_dir: str,
    client: LLMClient,
) -> tuple[int, list[dict]]:
    """Read files and run LLM extraction. Returns (component_id, valid_decisions)."""
    component_id = component["id"]
    component_name = component["name"]

    files = _read_component_source(source_dir, file_paths)
    if not files:
        return component_id, []

    total_chars = sum(len(v) for v in files.values())
    batches = [files] if total_chars <= TOKEN_BUDGET else _chunk_files(files, TOKEN_BUDGET)

    all_decisions: list[list[dict]] = []
    for batch in batches:
        prompt = component_extraction_prompt(component_name, _format_file_contents(batch))
        response = client.query(prompt, system=COMPONENT_SYSTEM, max_tokens=4096)
        all_decisions.append(response.get("decisions", []))

    raw = (
        _merge_decisions(all_decisions) if len(all_decisions) > 1
        else (all_decisions[0] if all_decisions else [])
    )
    valid = [
        {**d, "text": d["text"].strip(), "detail": (d.get("detail") or "").strip() or None}
        for d in raw
        if d.get("category") in VALID_CATEGORIES and d.get("text", "").strip()
    ]
    return component_id, valid


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_component_source(source_dir: str, file_paths: list[str]) -> dict[str, str]:
    """Read source files from disk. Skip binary files. Truncate at 500 lines."""
    source = Path(source_dir)
    result = {}

    for path in file_paths:
        p = Path(path)
        if p.suffix.lower() in BINARY_EXTENSIONS:
            continue

        abs_path = p if p.is_absolute() else source / path
        if not abs_path.exists():
            continue

        try:
            text = abs_path.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            if len(lines) > MAX_FILE_LINES:
                lines = lines[:MAX_FILE_LINES]
                lines.append(f"... (truncated at {MAX_FILE_LINES} lines)")
            result[path] = "\n".join(lines)
        except Exception:
            continue

    return result


def _chunk_files(
    files: dict[str, str], token_budget: int = TOKEN_BUDGET
) -> list[dict[str, str]]:
    """Split files into batches under token_budget chars. Files are never split."""
    batches: list[dict[str, str]] = []
    current_batch: dict[str, str] = {}
    current_size = 0

    for path, content in files.items():
        size = len(content)
        if current_batch and current_size + size > token_budget:
            batches.append(current_batch)
            current_batch = {}
            current_size = 0
        current_batch[path] = content
        current_size += size

    if current_batch:
        batches.append(current_batch)

    return batches


def _merge_decisions(batches: list[list[dict]]) -> list[dict]:
    """Deduplicate decisions across batch results by category + normalized text."""
    seen: set[tuple[str, str]] = set()
    merged: list[dict] = []

    for batch in batches:
        for decision in batch:
            key = (decision.get("category", ""), _normalize(decision.get("text", "")))
            if key not in seen:
                seen.add(key)
                merged.append(decision)

    return merged


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _format_file_contents(files: dict[str, str]) -> str:
    parts = []
    for path, content in files.items():
        parts.append(f"--- file: {path} ---\n{content}")
    return "\n\n".join(parts)
