"""Tests for db.transaction / _commit transaction infrastructure and atomic ingest."""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db


class TestTransaction:
    def test_commits_on_success(self, mem_conn):
        with db.transaction(mem_conn):
            db.add_module(mem_conn, "M1", "module", None, None, "in-repo", None)
        # New connection view via fresh query should see committed row
        assert len(db.get_modules(mem_conn)) == 1

    def test_rolls_back_on_exception(self, mem_conn):
        with pytest.raises(RuntimeError):
            with db.transaction(mem_conn):
                db.add_module(mem_conn, "M1", "module", None, None, "in-repo", None)
                raise RuntimeError("boom")
        assert db.get_modules(mem_conn) == []

    def test_reentrant(self, mem_conn):
        with db.transaction(mem_conn):
            db.add_module(mem_conn, "Outer", "module", None, None, "in-repo", None)
            with db.transaction(mem_conn):
                db.add_module(mem_conn, "Inner", "module", None, None, "in-repo", None)
            # Inner block must NOT have committed on its own
        assert {m["name"] for m in db.get_modules(mem_conn)} == {"Outer", "Inner"}

    def test_reentrant_inner_exception_rolls_back_all(self, mem_conn):
        with pytest.raises(RuntimeError):
            with db.transaction(mem_conn):
                db.add_module(mem_conn, "Outer", "module", None, None, "in-repo", None)
                with db.transaction(mem_conn):
                    db.add_module(mem_conn, "Inner", "module", None, None, "in-repo", None)
                raise RuntimeError("boom")
        assert db.get_modules(mem_conn) == []

    def test_helpers_autocommit_outside(self, mem_conn):
        """Outside a managed transaction, each helper commits itself (legacy behavior)."""
        db.add_module(mem_conn, "M1", "module", None, None, "in-repo", None)
        assert len(db.get_modules(mem_conn)) == 1


class TestAtomicIngest:
    def test_failed_ingest_rolls_back_old_map_intact(self, mem_conn, monkeypatch):
        """A mid-reconcile failure leaves the existing map + human decision intact."""
        run_id = db.start_pipeline_run(mem_conn, "seed")
        m1 = db.add_module(mem_conn, "Auth", "module", "svc", "py", "in-repo", None, run_id)
        db.add_decision(mem_conn, "tech", "human choice", module_id=m1, source="human", run_id=run_id)

        original = db.add_module_directories
        calls = {"n": 0}

        def flaky(conn, module_id, paths):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise RuntimeError("disk full")
            return original(conn, module_id, paths)

        monkeypatch.setattr(db, "add_module_directories", flaky)

        incoming = [
            {"name": "Auth", "classification": "module", "type": None, "technology": None,
             "source_origin": "in-repo", "deployment_target": None, "directories": ["src/auth"]},
            {"name": "Billing", "classification": "module", "type": None, "technology": None,
             "source_origin": "in-repo", "deployment_target": None, "directories": ["src/billing"]},
        ]
        with pytest.raises(RuntimeError):
            with db.transaction(mem_conn):
                db.reconcile_modules(mem_conn, incoming, run_id)

        # Old map survives: only Auth, no Billing; human decision intact
        names = {m["name"] for m in db.get_modules(mem_conn)}
        assert names == {"Auth"}
        decs = db.get_decisions(mem_conn, module_id=m1)
        assert len(decs) == 1
        assert decs[0]["text"] == "human choice"

    def test_failed_import_rolls_back(self, tmp_path):
        """POST /api/map/import with a malformed 2nd module -> 500, and the
        previous map survives (clear-and-replace is atomic)."""
        from fastapi.testclient import TestClient
        import main

        db_file = tmp_path / "test.db"
        original = main.DB_PATH
        main.DB_PATH = db_file
        try:
            conn = db.connect(str(db_file))
            db.init_schema(conn)
            db.add_module(conn, "Original", "module", None, None, "in-repo", None)
            db.close(conn)

            tc = TestClient(main.app)
            bad = {"modules": [
                {"id": 1, "name": "New", "components": []},
                {"id": 2, "components": []},  # missing 'name'
            ]}
            resp = tc.post("/api/map/import", json=bad)
            assert resp.status_code == 500

            # Original map survives
            got = tc.get("/api/map").json()
            assert {m["name"] for m in got["modules"]} == {"Original"}
        finally:
            main.DB_PATH = original
