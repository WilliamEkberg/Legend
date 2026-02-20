# Doc: Natural_Language_Code/revalidation/info_revalidation.md
"""
Re-validation pipeline entry point.

Orchestrates:
1. Before-snapshot (map version)
2. Phase 0: new file classification (detect untracked files, assign to components)
3. Phase A: component re-validation
4. Phase B: module re-validation
5. After-snapshot (map version)
"""

import db
from map_descriptions.llm_client import LLMClient
from revalidation.new_file_classifier import classify_new_files
from revalidation.component_revalidator import revalidate_all_components
from revalidation.module_revalidator import revalidate_all_modules


def run_revalidation_pipeline(
    db_path: str,
    source_dir: str,
    model: str,
    api_key: str,
) -> None:
    """
    Run the full re-validation pipeline.

    Args:
        db_path:    Path to the SQLite database file.
        source_dir: Root of the source directory being analyzed.
        model:      Model identifier (litellm format).
        api_key:    LLM provider API key.
    """
    conn = db.connect(db_path)
    try:
        db.init_schema(conn)

        run_id = db.start_pipeline_run(conn, "revalidation")
        client = LLMClient(model=model, api_key=api_key)

        try:
            # Before-snapshot
            print("[Re-validate] Creating before-snapshot...")
            before_version_id = db.create_map_version(conn, trigger="revalidation", run_id=run_id)
            print(f"[Re-validate] Before-snapshot: version {before_version_id}")

            # Start validation run
            val_run_id = db.start_validation_run(conn, before_version_id, model, run_id)

            # Phase 0: New file classification
            print("[Re-validate] Phase 0: New file classification...")
            phase0_stats = classify_new_files(conn, source_dir, client)

            # Phase A: Component re-validation
            print("[Re-validate] Phase A: Component re-validation...")
            comp_stats = revalidate_all_components(conn, source_dir, client, val_run_id)

            # Phase B: Module re-validation
            print("[Re-validate] Phase B: Module re-validation...")
            mod_stats = revalidate_all_modules(conn, client, val_run_id)

            # After-snapshot
            print("[Re-validate] Creating after-snapshot...")
            after_version_id = db.create_map_version(conn, trigger="revalidation", run_id=run_id)
            print(f"[Re-validate] After-snapshot: version {after_version_id}")

            # Merge stats
            summary = {}
            for key in ("confirmed", "updated", "outdated", "new", "implemented", "diverged", "unchanged"):
                summary[key] = comp_stats.get(key, 0) + mod_stats.get(key, 0)
            summary["new_file_paths"] = phase0_stats.get("new_file_paths", [])

            db.complete_validation_run(conn, val_run_id, after_version_id, "completed", summary)
            db.complete_pipeline_run(conn, run_id, "completed")

            stats = client.get_usage_stats()
            print(
                f"[Re-validate] Done. "
                f"confirmed={summary['confirmed']}, updated={summary['updated']}, "
                f"outdated={summary['outdated']}, new={summary['new']}, "
                f"implemented={summary['implemented']}, diverged={summary['diverged']}, "
                f"unchanged={summary['unchanged']}. "
                f"Tokens: {stats['total_tokens']:,} "
                f"(in: {stats['input_tokens']:,}, out: {stats['output_tokens']:,})"
            )

        except Exception:
            db.complete_pipeline_run(conn, run_id, "failed")
            raise

    finally:
        db.close(conn)
