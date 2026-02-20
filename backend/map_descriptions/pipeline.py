# Doc: Natural_Language_Code/map_descriptions/info_map_descriptions.md
"""
Part 3 pipeline entry point.

Orchestrates:
1. Clear previous pipeline-generated decisions (re-run safe)
2. Phase A: component descriptions
3. Phase B: module descriptions
"""

import db
from map_descriptions.llm_client import LLMClient
from map_descriptions.component_describer import describe_all_components
from map_descriptions.module_describer import describe_all_modules


def run_descriptions_pipeline(
    db_path: str,
    source_dir: str,
    model: str,
    api_key: str,
) -> None:
    """
    Run the full Part 3 descriptions pipeline.

    Args:
        db_path:    Path to the SQLite database file.
        source_dir: Root of the source directory being analyzed.
        model:      Model identifier (litellm format, e.g. 'anthropic/claude-sonnet-4-20250514').
        api_key:    LLM provider API key.
    """
    conn = db.connect(db_path)
    try:
        db.init_schema(conn)

        # Re-run safety: clear only pipeline-generated decisions
        print("[Part 3] Clearing previous pipeline-generated decisions...")
        db.clear_decisions(conn, source="pipeline_generated")

        run_id = db.start_pipeline_run(conn, "part3")
        client = LLMClient(model=model, api_key=api_key)

        try:
            print("[Part 3] Phase A: Component descriptions...")
            describe_all_components(conn, source_dir, client, run_id)

            print("[Part 3] Phase B: Module descriptions...")
            describe_all_modules(conn, source_dir, client, run_id)

            db.complete_pipeline_run(conn, run_id, "completed")

            # Auto-snapshot: capture decision state after Part 3
            version_id = db.create_map_version(conn, trigger="part3", run_id=run_id)
            print(f"[Part 3] Snapshot created: version {version_id}")

            stats = client.get_usage_stats()
            print(f"[Part 3] Done. Tokens used: {stats['total_tokens']:,} "
                  f"(in: {stats['input_tokens']:,}, out: {stats['output_tokens']:,})")

        except Exception:
            db.complete_pipeline_run(conn, run_id, "failed")
            raise

    finally:
        db.close(conn)
