# Doc: Natural_Language_Code/revalidation/info_revalidation.md
"""
Component Re-validation — Phase A.

For each component: read source files + existing decisions -> LLM comparison -> selective updates.

Parallelism strategy (same as component_describer.py):
1. Pre-read all component data from DB (main thread, serial).
2. Fire LLM calls concurrently via ThreadPoolExecutor (MAX_WORKERS).
   Workers have no DB access — safe to run in any thread.
3. Collect results and write to DB serially (main thread).
"""

import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed

import db
from map_descriptions.component_describer import (
    _read_component_source,
    _chunk_files,
    _format_file_contents,
    VALID_CATEGORIES,
    TOKEN_BUDGET,
)
from map_descriptions.llm_client import LLMClient
from revalidation.prompts import (
    COMPONENT_REVALIDATION_SYSTEM,
    component_revalidation_prompt,
    HUMAN_DECISION_CHECK_SYSTEM,
    human_decision_check_prompt,
)

MAX_WORKERS = 5


def revalidate_all_components(
    conn: sqlite3.Connection,
    source_dir: str,
    client: LLMClient,
    validation_run_id: int,
) -> dict:
    """
    Re-validate decisions for all eligible components.

    Returns summary stats: {confirmed, updated, outdated, new_added, implemented, diverged, unchanged}.
    """
    # Step 1: Pre-read all data from DB before spawning threads
    work_items: list[tuple[dict, list[str], list[dict], list[dict], str]] = []

    for module in db.get_modules(conn):
        if module.get("classification") == "supporting-asset":
            continue
        if module.get("source_origin") != "in-repo":
            continue

        module_name = module["name"]

        for component in db.get_components(conn, module["id"]):
            file_paths = db.get_component_files(conn, component["id"], exclude_test=True)
            if not file_paths:
                continue

            all_decisions = db.get_decisions(conn, component_id=component["id"])
            pipeline_decisions = [
                {"id": d["id"], "category": d["category"], "text": d["text"]}
                for d in all_decisions if d["source"] == "pipeline_generated"
            ]
            human_decisions = [
                {"id": d["id"], "category": d["category"], "text": d["text"]}
                for d in all_decisions if d["source"] == "human"
            ]

            if not pipeline_decisions and not human_decisions:
                continue

            work_items.append((
                component, file_paths, pipeline_decisions, human_decisions, module_name
            ))

    if not work_items:
        print("  [Re-validate] Phase A: no eligible components found")
        return _empty_stats()

    print(f"  [Re-validate] Phase A: {len(work_items)} components ({MAX_WORKERS} parallel)...")

    # Step 2: LLM calls in parallel — workers do NOT touch the DB
    results: list[tuple[dict, str, list[dict]]] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_comp = {
            executor.submit(
                _revalidate_component_worker,
                component, file_paths, pipeline_decisions, human_decisions,
                module_name, source_dir, client,
            ): component
            for component, file_paths, pipeline_decisions, human_decisions, module_name
            in work_items
        }
        for future in as_completed(future_to_comp):
            component = future_to_comp[future]
            try:
                comp, mod_name, validations = future.result()
                results.append((comp, mod_name, validations))
                non_confirmed = [v for v in validations if v["status"] != "confirmed" and v["status"] != "unchanged"]
                print(f"  [Re-validate]   {component['name']}: {len(validations)} checked, {len(non_confirmed)} changed")
            except Exception as e:
                print(f"  [Re-validate]   Warning: {component['name']} failed: {e}")

    # Step 3: Write to DB serially
    stats = _empty_stats()
    for component, module_name, validations in results:
        comp_name = component["name"]
        for v in validations:
            decision_id = v.get("decision_id")
            status = v["status"]
            source = v.get("source", "pipeline_generated")

            db.add_decision_validation(
                conn,
                validation_run_id=validation_run_id,
                decision_id=decision_id,
                source=source,
                status=status,
                old_text=v.get("old_text"),
                new_text=v.get("new_text"),
                reason=v.get("reason"),
                category=v.get("category"),
                module_name=module_name,
                component_name=comp_name,
            )

            # Apply updates to live decisions (without change_records!)
            if status == "updated" and v.get("new_text") and decision_id:
                db.update_decision(conn, decision_id, text=v["new_text"])

            # Add new decisions discovered during re-validation
            if status == "new" and v.get("text"):
                new_id = db.add_decision(
                    conn,
                    category=v["category"],
                    text=v["text"],
                    component_id=component["id"],
                    source="pipeline_generated",
                )
                # Update the validation record with the new decision_id
                # (it was added with decision_id=None for 'new' entries)

            stats[status] = stats.get(status, 0) + 1

    print(f"  [Re-validate] Phase A complete: {sum(stats.values())} validations")
    return stats


# ---------------------------------------------------------------------------
# Worker — no DB access, safe to call from any thread
# ---------------------------------------------------------------------------

def _revalidate_component_worker(
    component: dict,
    file_paths: list[str],
    pipeline_decisions: list[dict],
    human_decisions: list[dict],
    module_name: str,
    source_dir: str,
    client: LLMClient,
) -> tuple[dict, str, list[dict]]:
    """
    Run re-validation LLM calls for one component.
    Returns (component, module_name, validations_list).
    """
    files = _read_component_source(source_dir, file_paths)
    if not files:
        return component, module_name, []

    total_chars = sum(len(v) for v in files.values())
    file_contents = _format_file_contents(
        files if total_chars <= TOKEN_BUDGET
        else {k: v for batch in _chunk_files(files, TOKEN_BUDGET) for k, v in batch.items()}
    )

    validations: list[dict] = []

    # Validate pipeline-generated decisions
    if pipeline_decisions:
        prompt = component_revalidation_prompt(
            component["name"], pipeline_decisions, file_contents,
        )
        response = client.query(prompt, system=COMPONENT_REVALIDATION_SYSTEM, max_tokens=4096)

        for v in response.get("validations", []):
            decision_id = v.get("decision_id")
            status = v.get("status", "confirmed")
            # Find the original decision text for 'updated' entries
            original = next((d for d in pipeline_decisions if d["id"] == decision_id), None)
            validations.append({
                "decision_id": decision_id,
                "source": "pipeline_generated",
                "status": status,
                "old_text": original["text"] if original and status == "updated" else None,
                "new_text": v.get("new_text"),
                "reason": v.get("reason"),
                "category": original["category"] if original else None,
            })

        # New decisions not captured by existing ones
        for nd in response.get("new_decisions", []):
            cat = nd.get("category", "")
            text = nd.get("text", "").strip()
            if cat in VALID_CATEGORIES and text:
                validations.append({
                    "decision_id": None,
                    "source": "pipeline_generated",
                    "status": "new",
                    "old_text": None,
                    "new_text": None,
                    "reason": None,
                    "category": cat,
                    "text": text,
                })

    # Check human decisions for implementation status
    if human_decisions:
        prompt = human_decision_check_prompt(
            component["name"], human_decisions, file_contents,
        )
        response = client.query(prompt, system=HUMAN_DECISION_CHECK_SYSTEM, max_tokens=2048)

        for v in response.get("validations", []):
            decision_id = v.get("decision_id")
            original = next((d for d in human_decisions if d["id"] == decision_id), None)
            validations.append({
                "decision_id": decision_id,
                "source": "human",
                "status": v.get("status", "unchanged"),
                "old_text": None,
                "new_text": None,
                "reason": v.get("reason"),
                "category": original["category"] if original else None,
            })

    return component, module_name, validations


def _empty_stats() -> dict:
    return {
        "confirmed": 0, "updated": 0, "outdated": 0, "new": 0,
        "implemented": 0, "diverged": 0, "unchanged": 0,
    }
