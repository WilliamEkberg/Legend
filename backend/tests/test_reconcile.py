"""Tests for reconcile-in-place, parking/reattach of human decisions, and migration."""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db


def _mod(name, dirs=None, source_origin="in-repo"):
    return {
        "name": name, "classification": "module", "type": None, "technology": None,
        "source_origin": source_origin, "deployment_target": None, "directories": dirs or [],
    }


def _reconcile_modules(conn, incoming, run_id):
    with db.transaction(conn):
        return db.reconcile_modules(conn, incoming, run_id)


def _reconcile_components(conn, module_id, incoming, run_id):
    with db.transaction(conn):
        return db.reconcile_components(conn, module_id, incoming, run_id)


class TestReconcileModules:
    def test_rerun_preserves_human_decision_on_matched_module(self, mem_conn):
        run1 = db.start_pipeline_run(mem_conn, "r1")
        ids = _reconcile_modules(mem_conn, [_mod("Auth", ["src/auth"])], run1)
        auth_id = ids["Auth"]
        db.add_decision(mem_conn, "tech", "human note", module_id=auth_id, source="human")

        run2 = db.start_pipeline_run(mem_conn, "r2")
        ids2 = _reconcile_modules(mem_conn, [_mod("Auth", ["src/auth", "src/auth/v2"])], run2)
        # Same ID kept
        assert ids2["Auth"] == auth_id
        decs = db.get_decisions(mem_conn, module_id=auth_id)
        assert len(decs) == 1 and decs[0]["text"] == "human note"

    def test_rerun_keeps_human_created_entities(self, mem_conn):
        # Human-created module (pipeline_run_id NULL) must survive a pipeline re-run
        human_mod = db.add_module(mem_conn, "HumanMod", "module", None, None, "in-repo", None)
        run1 = db.start_pipeline_run(mem_conn, "r1")
        _reconcile_modules(mem_conn, [_mod("Auth")], run1)
        names = {m["name"] for m in db.get_modules(mem_conn)}
        assert names == {"HumanMod", "Auth"}
        assert db.get_module(mem_conn, human_mod) is not None

    def test_rerun_parks_human_decisions_on_removed_module(self, mem_conn):
        run1 = db.start_pipeline_run(mem_conn, "r1")
        ids = _reconcile_modules(mem_conn, [_mod("Auth"), _mod("Billing")], run1)
        db.add_decision(mem_conn, "tech", "keep me", module_id=ids["Billing"], source="human")
        # Pipeline-generated decision on Billing should NOT be parked (cascades away)
        db.add_decision(mem_conn, "tech", "pipeline note", module_id=ids["Billing"],
                        source="pipeline_generated", run_id=run1)

        run2 = db.start_pipeline_run(mem_conn, "r2")
        _reconcile_modules(mem_conn, [_mod("Auth")], run2)  # Billing removed

        assert {m["name"] for m in db.get_modules(mem_conn)} == {"Auth"}
        orphaned = db.get_orphaned_decisions(mem_conn)
        assert len(orphaned) == 1
        assert orphaned[0]["text"] == "keep me"
        assert orphaned[0]["orphaned_module_name"] == "Billing"
        assert orphaned[0]["orphaned_component_name"] is None

    def test_reattach_orphan_when_entity_returns(self, mem_conn):
        run1 = db.start_pipeline_run(mem_conn, "r1")
        ids = _reconcile_modules(mem_conn, [_mod("Auth"), _mod("Billing")], run1)
        db.add_decision(mem_conn, "tech", "keep me", module_id=ids["Billing"], source="human")

        run2 = db.start_pipeline_run(mem_conn, "r2")
        _reconcile_modules(mem_conn, [_mod("Auth")], run2)
        assert len(db.get_orphaned_decisions(mem_conn)) == 1

        run3 = db.start_pipeline_run(mem_conn, "r3")
        ids3 = _reconcile_modules(mem_conn, [_mod("Auth"), _mod("Billing")], run3)
        assert db.get_orphaned_decisions(mem_conn) == []
        decs = db.get_decisions(mem_conn, module_id=ids3["Billing"])
        assert any(d["text"] == "keep me" and d["source"] == "human" for d in decs)

    def test_change_records_stay_valid_across_rerun(self, mem_conn):
        run1 = db.start_pipeline_run(mem_conn, "r1")
        ids = _reconcile_modules(mem_conn, [_mod("Auth")], run1)
        auth_id = ids["Auth"]
        cr_id = db.add_change_record(
            mem_conn, "module", auth_id, "edit", None, '{"x":1}', "human", module_id=auth_id
        )
        run2 = db.start_pipeline_run(mem_conn, "r2")
        ids2 = _reconcile_modules(mem_conn, [_mod("Auth")], run2)
        assert ids2["Auth"] == auth_id
        records = db.get_change_records(mem_conn)
        assert any(r["id"] == cr_id and r["entity_id"] == auth_id for r in records)

    def test_pipeline_module_edges_replaced_human_edges_kept(self, mem_conn):
        run1 = db.start_pipeline_run(mem_conn, "r1")
        ids = _reconcile_modules(mem_conn, [_mod("A"), _mod("B"), _mod("C")], run1)
        # pipeline edge (has run_id) and a human edge (run_id NULL)
        db.upsert_module_edge(mem_conn, ids["A"], ids["B"], "depends_on", 1.0, None, run1)
        db.add_module_edge(mem_conn, ids["A"], ids["C"], "depends_on", 1.0, None, None)

        # Simulate ingest edge replacement
        with db.transaction(mem_conn):
            mem_conn.execute("DELETE FROM module_edges WHERE pipeline_run_id IS NOT NULL")
            run2 = db.start_pipeline_run(mem_conn, "r2")
            db.upsert_module_edge(mem_conn, ids["B"], ids["C"], "depends_on", 1.0, None, run2)

        edges = db.get_module_edges(mem_conn)
        pairs = {(e["source_id"], e["target_id"]) for e in edges}
        assert (ids["A"], ids["C"]) in pairs  # human edge kept
        assert (ids["B"], ids["C"]) in pairs  # new pipeline edge
        assert (ids["A"], ids["B"]) not in pairs  # old pipeline edge removed


class TestReconcileComponents:
    def test_rerun_preserves_human_decision_on_matched_component(self, mem_conn):
        run1 = db.start_pipeline_run(mem_conn, "r1")
        mid = db.add_module(mem_conn, "Auth", "module", None, None, "in-repo", None, run1)
        cids = _reconcile_components(
            mem_conn, mid, [{"name": "Login", "purpose": "p", "confidence": 0.9,
                             "files": [("a.py", False)]}], run1)
        login_id = cids["Login"]
        db.add_decision(mem_conn, "tech", "human comp note", component_id=login_id, source="human")

        run2 = db.start_pipeline_run(mem_conn, "r2")
        cids2 = _reconcile_components(
            mem_conn, mid, [{"name": "Login", "purpose": "p2", "confidence": 0.8,
                             "files": [("a.py", False), ("b.py", False)]}], run2)
        assert cids2["Login"] == login_id
        decs = db.get_decisions(mem_conn, component_id=login_id)
        assert len(decs) == 1 and decs[0]["text"] == "human comp note"

    def test_rerun_parks_component_decision_with_both_names(self, mem_conn):
        run1 = db.start_pipeline_run(mem_conn, "r1")
        mid = db.add_module(mem_conn, "Auth", "module", None, None, "in-repo", None, run1)
        cids = _reconcile_components(
            mem_conn, mid,
            [{"name": "Login", "purpose": "p", "confidence": 0.9, "files": []},
             {"name": "Token", "purpose": "p", "confidence": 0.9, "files": []}], run1)
        db.add_decision(mem_conn, "tech", "comp keep", component_id=cids["Token"], source="human")

        run2 = db.start_pipeline_run(mem_conn, "r2")
        _reconcile_components(
            mem_conn, mid, [{"name": "Login", "purpose": "p", "confidence": 0.9, "files": []}], run2)

        orphaned = db.get_orphaned_decisions(mem_conn)
        assert len(orphaned) == 1
        assert orphaned[0]["orphaned_module_name"] == "Auth"
        assert orphaned[0]["orphaned_component_name"] == "Token"


class TestMigration:
    def test_migration_rebuilds_decisions_table(self, tmp_path):
        """Create a DB with the OLD decisions DDL, insert rows, run init_schema,
        assert rows intact + IDs preserved + new cols exist + parking accepted."""
        db_file = tmp_path / "old.db"
        conn = sqlite3.connect(str(db_file))
        conn.row_factory = sqlite3.Row
        # Minimal old schema: pipeline_runs, modules, components, and OLD decisions
        conn.executescript("""
            CREATE TABLE pipeline_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, step TEXT,
                started_at TEXT, completed_at TEXT, status TEXT, metadata TEXT);
            CREATE TABLE modules (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
                classification TEXT, type TEXT, technology TEXT, source_origin TEXT,
                deployment_target TEXT, pipeline_run_id INTEGER, created_at TEXT);
            CREATE TABLE components (id INTEGER PRIMARY KEY AUTOINCREMENT, module_id INTEGER,
                name TEXT, purpose TEXT, confidence REAL, pipeline_run_id INTEGER, created_at TEXT,
                UNIQUE(module_id, name));
            CREATE TABLE decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                module_id INTEGER REFERENCES modules(id) ON DELETE CASCADE,
                component_id INTEGER REFERENCES components(id) ON DELETE CASCADE,
                category TEXT NOT NULL, text TEXT NOT NULL, detail TEXT,
                source TEXT NOT NULL DEFAULT 'pipeline_generated',
                pipeline_run_id INTEGER, created_at TEXT NOT NULL DEFAULT (datetime('now')),
                CHECK ((module_id IS NOT NULL AND component_id IS NULL) OR
                       (module_id IS NULL AND component_id IS NOT NULL))
            );
        """)
        conn.execute("INSERT INTO modules (id, name) VALUES (7, 'Auth')")
        conn.execute("INSERT INTO decisions (id, module_id, category, text, source) "
                     "VALUES (42, 7, 'tech', 'kept row', 'human')")
        conn.commit()

        # Run full init_schema which triggers _migrate
        db.init_schema(conn)

        cols = [r[1] for r in conn.execute("PRAGMA table_info(decisions)").fetchall()]
        assert "orphaned_module_name" in cols
        assert "orphaned_component_name" in cols

        row = conn.execute("SELECT * FROM decisions WHERE id = 42").fetchone()
        assert row is not None
        assert row["text"] == "kept row"
        assert row["module_id"] == 7  # ID + FK preserved

        # New relaxed CHECK accepts a parked decision (both NULL)
        conn.execute("INSERT INTO decisions (module_id, component_id, category, text, source,"
                     " orphaned_module_name) VALUES (NULL, NULL, 'tech', 'parked', 'human', 'Auth')")
        conn.commit()
        parked = conn.execute("SELECT * FROM decisions WHERE text = 'parked'").fetchone()
        assert parked["orphaned_module_name"] == "Auth"
        conn.close()
