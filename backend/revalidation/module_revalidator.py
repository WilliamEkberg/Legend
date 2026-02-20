# Doc: Natural_Language_Code/revalidation/info_revalidation.md
"""
Module Re-validation — Phase B.

For each module: collect component decisions + existing module decisions -> LLM comparison.

Module validation takes component KEY DECISIONS as input (not code),
matching the Part 3 module elevation data flow.

Parallelism strategy (same as module_describer.py):
1. Pre-read all component decisions and module decisions from DB (main thread, serial).
2. Run module workers in parallel (MAX_WORKERS concurrent).
3. Write results to DB serially (main thread).
"""

import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed

import db
from map_descriptions.llm_client import LLMClient
from map_descriptions.module_describer import (
    _collect_component_decisions,
    _format_component_decisions,
)
from revalidation.prompts import (
    MODULE_REVALIDATION_SYSTEM,
    module_revalidation_prompt,
    HUMAN_DECISION_CHECK_SYSTEM,
    human_decision_check_prompt,
)

MAX_WORKERS = 5


def revalidate_all_modules(
    conn: sqlite3.Connection,
    client: LLMClient,
    validation_run_id: int,
) -> dict:
    """
    Re-validate module-level decisions for all eligible modules.

    Returns summary stats: {confirmed, updated, outdated, new_added, implemented, diverged, unchanged}.
    """
    eligible = [
        m for m in db.get_modules(conn)
        if m.get("classification") != "supporting-asset"
        and m.get("source_origin") == "in-repo"
    ]

    if not eligible:
        print("  [Re-validate] Phase B: no eligible modules found")
        return _empty_stats()

    # Pre-read all data from DB
    module_inputs: list[tuple[dict, dict, list[dict], list[dict]]] = []
    for module in eligible:
        component_decisions = _collect_component_decisions(conn, module["id"])
        all_module_decisions = db.get_decisions(conn, module_id=module["id"])

        pipeline_decisions = [
            {"id": d["id"], "category": d["category"], "text": d["text"]}
            for d in all_module_decisions if d["source"] == "pipeline_generated"
        ]
        human_decisions = [
            {"id": d["id"], "category": d["category"], "text": d["text"]}
            for d in all_module_decisions if d["source"] == "human"
        ]

        if not pipeline_decisions and not human_decisions:
            continue

        module_inputs.append((module, component_decisions, pipeline_decisions, human_decisions))

    if not module_inputs:
        print("  [Re-validate] Phase B: no modules with decisions to validate")
        return _empty_stats()

    print(f"  [Re-validate] Phase B: {len(module_inputs)} modules ({MAX_WORKERS} parallel)...")

    # LLM calls in parallel
    results: list[tuple[dict, list[dict]]] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_module = {
            executor.submit(
                _revalidate_module_worker,
                module, component_decisions, pipeline_decisions, human_decisions, client,
            ): module
            for module, component_decisions, pipeline_decisions, human_decisions
            in module_inputs
        }
        for future in as_completed(future_to_module):
            module = future_to_module[future]
            try:
                mod, validations = future.result()
                results.append((mod, validations))
                non_confirmed = [v for v in validations if v["status"] != "confirmed" and v["status"] != "unchanged"]
                print(f"  [Re-validate]   {module['name']}: {len(validations)} checked, {len(non_confirmed)} changed")
            except Exception as e:
                print(f"  [Re-validate]   Warning: module {module['name']} failed: {e}")

    # Write to DB serially
    stats = _empty_stats()
    for module, validations in results:
        for v in validations:
            decision_id = v.get("decision_id")
            status = v["status"]

            db.add_decision_validation(
                conn,
                validation_run_id=validation_run_id,
                decision_id=decision_id,
                source=v.get("source", "pipeline_generated"),
                status=status,
                old_text=v.get("old_text"),
                new_text=v.get("new_text"),
                reason=v.get("reason"),
                category=v.get("category"),
                module_name=module["name"],
                component_name=None,
            )

            # Apply updates to live decisions (without change_records!)
            if status == "updated" and v.get("new_text") and decision_id:
                db.update_decision(conn, decision_id, text=v["new_text"])

            # Add new decisions discovered during re-validation
            if status == "new" and v.get("text"):
                db.add_decision(
                    conn,
                    category=v["category"],
                    text=v["text"],
                    module_id=module["id"],
                    source="pipeline_generated",
                )

            stats[status] = stats.get(status, 0) + 1

    print(f"  [Re-validate] Phase B complete: {sum(stats.values())} validations")
    return stats


# ---------------------------------------------------------------------------
# Worker — no DB access, safe to call from any thread
# ---------------------------------------------------------------------------

def _revalidate_module_worker(
    module: dict,
    component_decisions: dict[str, list[dict]],
    pipeline_decisions: list[dict],
    human_decisions: list[dict],
    client: LLMClient,
) -> tuple[dict, list[dict]]:
    """
    Run re-validation LLM calls for one module.
    Returns (module, validations_list).
    """
    validations: list[dict] = []

    # Validate pipeline-generated module decisions against component decisions
    if pipeline_decisions and component_decisions:
        decisions_text = _format_component_decisions(component_decisions)
        prompt = module_revalidation_prompt(
            module["name"], pipeline_decisions, decisions_text,
        )
        response = client.query(prompt, system=MODULE_REVALIDATION_SYSTEM, max_tokens=4096)

        for v in response.get("validations", []):
            decision_id = v.get("decision_id")
            status = v.get("status", "confirmed")
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

        # New module-level decisions
        for nd in response.get("new_decisions", []):
            text = nd.get("text", "").strip()
            cat = nd.get("category", "cross_cutting")
            if text:
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

    # Check human module decisions
    # For module-level human decisions, we use component decisions as the "source code"
    # since module decisions are derived from component decisions, not code directly
    if human_decisions and component_decisions:
        decisions_text = _format_component_decisions(component_decisions)
        prompt = human_decision_check_prompt(
            module["name"], human_decisions, decisions_text,
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

    return module, validations


def _empty_stats() -> dict:
    return {
        "confirmed": 0, "updated": 0, "outdated": 0, "new": 0,
        "implemented": 0, "diverged": 0, "unchanged": 0,
    }
