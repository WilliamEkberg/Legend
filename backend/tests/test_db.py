"""Tests for db.py CRUD operations."""

import sqlite3
import json

import pytest

import db


# ---------------------------------------------------------------------------
# Schema & connection
# ---------------------------------------------------------------------------

class TestConnection:
    def test_schema_creates_all_tables(self, mem_conn):
        """All 14 tables should exist after init_schema."""
        rows = mem_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        table_names = {r["name"] for r in rows}
        expected = {
            "pipeline_runs", "modules", "module_directories", "components",
            "component_files", "module_edges", "component_edges", "decisions",
            "change_records", "baselines", "tickets", "ticket_files",
            "ticket_change_records", "ticket_decisions",
        }
        assert expected.issubset(table_names)

    def test_row_factory_returns_dicts(self, mem_conn):
        """Rows should be accessible by column name."""
        rid = db.start_pipeline_run(mem_conn, "test")
        rows = mem_conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (rid,)).fetchall()
        assert rows[0]["step"] == "test"


# ---------------------------------------------------------------------------
# Pipeline runs
# ---------------------------------------------------------------------------

class TestPipelineRuns:
    def test_start_returns_id(self, mem_conn):
        rid = db.start_pipeline_run(mem_conn, "indexing")
        assert isinstance(rid, int) and rid > 0

    def test_complete_sets_status(self, mem_conn):
        rid = db.start_pipeline_run(mem_conn, "indexing")
        db.complete_pipeline_run(mem_conn, rid, "completed")
        row = mem_conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (rid,)).fetchone()
        assert row["status"] == "completed"
        assert row["completed_at"] is not None

    def test_complete_with_failure(self, mem_conn):
        rid = db.start_pipeline_run(mem_conn, "indexing")
        db.complete_pipeline_run(mem_conn, rid, "failed")
        row = mem_conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (rid,)).fetchone()
        assert row["status"] == "failed"


# ---------------------------------------------------------------------------
# Modules
# ---------------------------------------------------------------------------

class TestModules:
    def test_add_module_returns_id(self, mem_conn):
        mid = db.add_module(mem_conn, "MyMod", "module", "service", "Python", "in-repo", "k8s")
        assert isinstance(mid, int) and mid > 0

    def test_get_modules_all(self, seeded_conn):
        mods = db.get_modules(seeded_conn)
        assert len(mods) == 2

    def test_get_modules_by_classification(self, seeded_conn):
        mods = db.get_modules(seeded_conn, classification="shared-library")
        assert len(mods) == 1
        assert mods[0]["name"] == "Shared Utils"

    def test_get_module_by_id(self, seeded_conn):
        mods = db.get_modules(seeded_conn)
        m = db.get_module(seeded_conn, mods[0]["id"])
        assert m is not None
        assert m["name"] == mods[0]["name"]

    def test_get_module_not_found(self, mem_conn):
        assert db.get_module(mem_conn, 9999) is None

    def test_clear_modules(self, seeded_conn):
        db.clear_modules(seeded_conn)
        assert db.get_modules(seeded_conn) == []

    def test_clear_modules_cascades_components(self, seeded_conn):
        db.clear_modules(seeded_conn)
        assert db.get_components(seeded_conn) == []

    def test_clear_modules_cascades_module_edges(self, seeded_conn):
        assert len(db.get_module_edges(seeded_conn)) > 0
        db.clear_modules(seeded_conn)
        assert db.get_module_edges(seeded_conn) == []

    def test_clear_modules_cascades_component_edges(self, seeded_conn):
        assert len(db.get_component_edges(seeded_conn)) > 0
        db.clear_modules(seeded_conn)
        assert db.get_component_edges(seeded_conn) == []

    def test_unique_module_name(self, mem_conn):
        db.add_module(mem_conn, "Dup", "module", None, None, None, None)
        with pytest.raises(sqlite3.IntegrityError):
            db.add_module(mem_conn, "Dup", "module", None, None, None, None)


# ---------------------------------------------------------------------------
# Module directories
# ---------------------------------------------------------------------------

class TestModuleDirectories:
    def test_add_and_get(self, mem_conn):
        mid = db.add_module(mem_conn, "M", "module", None, None, None, None)
        db.add_module_directories(mem_conn, mid, ["src/a", "src/b"])
        dirs = db.get_module_directories(mem_conn, mid)
        assert set(dirs) == {"src/a", "src/b"}

    def test_empty_dirs(self, mem_conn):
        mid = db.add_module(mem_conn, "M", "module", None, None, None, None)
        assert db.get_module_directories(mem_conn, mid) == []


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------

class TestComponents:
    def test_add_component_returns_id(self, mem_conn):
        mid = db.add_module(mem_conn, "M", "module", None, None, None, None)
        cid = db.add_component(mem_conn, mid, "Comp", "purpose", 0.9)
        assert isinstance(cid, int) and cid > 0

    def test_get_components_by_module(self, seeded_conn):
        mods = db.get_modules(seeded_conn)
        auth_mod = [m for m in mods if m["name"] == "Auth Service"][0]
        comps = db.get_components(seeded_conn, module_id=auth_mod["id"])
        assert len(comps) == 2
        names = {c["name"] for c in comps}
        assert names == {"Login Handler", "Token Manager"}

    def test_get_components_all(self, seeded_conn):
        comps = db.get_components(seeded_conn)
        assert len(comps) == 3

    def test_get_component_by_id(self, seeded_conn):
        comps = db.get_components(seeded_conn)
        c = db.get_component(seeded_conn, comps[0]["id"])
        assert c is not None

    def test_get_component_not_found(self, mem_conn):
        assert db.get_component(mem_conn, 9999) is None

    def test_delete_component(self, seeded_conn):
        comps = db.get_components(seeded_conn)
        db.delete_component(seeded_conn, comps[0]["id"])
        assert db.get_component(seeded_conn, comps[0]["id"]) is None


# ---------------------------------------------------------------------------
# Component files
# ---------------------------------------------------------------------------

class TestComponentFiles:
    def test_add_and_get_files(self, mem_conn):
        mid = db.add_module(mem_conn, "M", "module", None, None, None, None)
        cid = db.add_component(mem_conn, mid, "C", None, None)
        db.add_component_files(mem_conn, cid, ["a.py", "b.py", "test_a.py"], [False, False, True])
        all_files = db.get_component_files(mem_conn, cid)
        assert len(all_files) == 3

    def test_exclude_test_files(self, mem_conn):
        mid = db.add_module(mem_conn, "M", "module", None, None, None, None)
        cid = db.add_component(mem_conn, mid, "C", None, None)
        db.add_component_files(mem_conn, cid, ["a.py", "test_a.py"], [False, True])
        source_only = db.get_component_files(mem_conn, cid, exclude_test=True)
        assert source_only == ["a.py"]

    def test_move_component_files(self, mem_conn):
        mid = db.add_module(mem_conn, "M", "module", None, None, None, None)
        c1 = db.add_component(mem_conn, mid, "C1", None, None)
        c2 = db.add_component(mem_conn, mid, "C2", None, None)
        db.add_component_files(mem_conn, c1, ["a.py", "b.py"], [False, False])
        db.move_component_files(mem_conn, ["a.py"], c1, c2)
        assert db.get_component_files(mem_conn, c1) == ["b.py"]
        assert db.get_component_files(mem_conn, c2) == ["a.py"]

    def test_move_empty_list_is_noop(self, mem_conn):
        mid = db.add_module(mem_conn, "M", "module", None, None, None, None)
        c1 = db.add_component(mem_conn, mid, "C1", None, None)
        c2 = db.add_component(mem_conn, mid, "C2", None, None)
        db.move_component_files(mem_conn, [], c1, c2)  # Should not raise


# ---------------------------------------------------------------------------
# Module edges
# ---------------------------------------------------------------------------

class TestModuleEdges:
    def test_add_and_get(self, mem_conn):
        m1 = db.add_module(mem_conn, "A", "module", None, None, None, None)
        m2 = db.add_module(mem_conn, "B", "module", None, None, None, None)
        db.add_module_edge(mem_conn, m1, m2, "depends_on", 2.5, '{"label":"uses B"}')
        edges = db.get_module_edges(mem_conn)
        assert len(edges) == 1
        assert edges[0]["weight"] == 2.5

    def test_get_by_source(self, seeded_conn):
        mods = db.get_modules(seeded_conn)
        auth = [m for m in mods if m["name"] == "Auth Service"][0]
        edges = db.get_module_edges(seeded_conn, source_id=auth["id"])
        assert len(edges) == 1

    def test_unique_module_edge(self, mem_conn):
        m1 = db.add_module(mem_conn, "A", "module", None, None, None, None)
        m2 = db.add_module(mem_conn, "B", "module", None, None, None, None)
        db.add_module_edge(mem_conn, m1, m2, "depends_on")
        with pytest.raises(sqlite3.IntegrityError):
            db.add_module_edge(mem_conn, m1, m2, "depends_on")


# ---------------------------------------------------------------------------
# Component edges
# ---------------------------------------------------------------------------

class TestComponentEdges:
    def test_add_and_get(self, mem_conn):
        mid = db.add_module(mem_conn, "M", "module", None, None, None, None)
        c1 = db.add_component(mem_conn, mid, "C1", None, None)
        c2 = db.add_component(mem_conn, mid, "C2", None, None)
        db.add_component_edge(mem_conn, c1, c2, "depends-on", 5.0, '{"label":"calls"}')
        edges = db.get_component_edges(mem_conn)
        assert len(edges) == 1
        assert edges[0]["weight"] == 5.0

    def test_get_by_source(self, seeded_conn):
        comps = db.get_components(seeded_conn)
        login = [c for c in comps if c["name"] == "Login Handler"][0]
        edges = db.get_component_edges(seeded_conn, source_id=login["id"])
        assert len(edges) == 1


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------

class TestDecisions:
    def test_add_module_decision(self, mem_conn):
        mid = db.add_module(mem_conn, "M", "module", None, None, None, None)
        did = db.add_decision(mem_conn, "pattern", "Use MVC", module_id=mid)
        assert isinstance(did, int) and did > 0

    def test_add_component_decision(self, mem_conn):
        mid = db.add_module(mem_conn, "M", "module", None, None, None, None)
        cid = db.add_component(mem_conn, mid, "C", None, None)
        did = db.add_decision(mem_conn, "tech", "Use Redis", component_id=cid)
        assert isinstance(did, int) and did > 0

    def test_get_decisions_by_module(self, seeded_conn):
        mods = db.get_modules(seeded_conn)
        auth = [m for m in mods if m["name"] == "Auth Service"][0]
        decs = db.get_decisions(seeded_conn, module_id=auth["id"])
        assert len(decs) == 1
        assert "JWT" in decs[0]["text"]

    def test_get_decisions_by_component(self, seeded_conn):
        comps = db.get_components(seeded_conn)
        login = [c for c in comps if c["name"] == "Login Handler"][0]
        decs = db.get_decisions(seeded_conn, component_id=login["id"])
        assert len(decs) == 1

    def test_get_decisions_all(self, seeded_conn):
        decs = db.get_decisions(seeded_conn)
        assert len(decs) == 2

    def test_get_decision_by_id(self, seeded_conn):
        decs = db.get_decisions(seeded_conn)
        d = db.get_decision(seeded_conn, decs[0]["id"])
        assert d is not None
        assert d["id"] == decs[0]["id"]

    def test_get_decision_not_found(self, mem_conn):
        assert db.get_decision(mem_conn, 9999) is None

    def test_update_decision(self, seeded_conn):
        decs = db.get_decisions(seeded_conn)
        did = decs[0]["id"]
        db.update_decision(seeded_conn, did, text="Updated text", source="human")
        updated = db.get_decision(seeded_conn, did)
        assert updated["text"] == "Updated text"
        assert updated["source"] == "human"

    def test_update_decision_no_fields_is_noop(self, seeded_conn):
        decs = db.get_decisions(seeded_conn)
        did = decs[0]["id"]
        old = db.get_decision(seeded_conn, did)
        db.update_decision(seeded_conn, did)  # No fields
        new = db.get_decision(seeded_conn, did)
        assert old["text"] == new["text"]

    def test_delete_decisions_by_ids(self, seeded_conn):
        decs = db.get_decisions(seeded_conn)
        did = decs[0]["id"]
        db.delete_decisions_by_ids(seeded_conn, [did])
        assert db.get_decision(seeded_conn, did) is None

    def test_delete_decisions_empty_list(self, mem_conn):
        db.delete_decisions_by_ids(mem_conn, [])  # Should not raise

    def test_clear_decisions_by_source(self, seeded_conn):
        # pipeline_generated decisions should be cleared, human should remain
        before_count = len(db.get_decisions(seeded_conn))
        assert before_count == 2  # one pipeline_generated, one human
        db.clear_decisions(seeded_conn, source="pipeline_generated")
        remaining = db.get_decisions(seeded_conn)
        assert len(remaining) == 1
        assert remaining[0]["source"] == "human"

    def test_decision_check_constraint_both_ids_fails(self, mem_conn):
        """Decision with BOTH module_id and component_id should violate CHECK."""
        mid = db.add_module(mem_conn, "M", "module", None, None, None, None)
        cid = db.add_component(mem_conn, mid, "C", None, None)
        with pytest.raises(sqlite3.IntegrityError):
            mem_conn.execute(
                "INSERT INTO decisions (module_id, component_id, category, text) VALUES (?, ?, ?, ?)",
                (mid, cid, "tech", "bad"),
            )

    def test_decision_check_constraint_no_ids_fails(self, mem_conn):
        """Decision with NEITHER module_id nor component_id should violate CHECK."""
        with pytest.raises(sqlite3.IntegrityError):
            mem_conn.execute(
                "INSERT INTO decisions (module_id, component_id, category, text) VALUES (?, ?, ?, ?)",
                (None, None, "tech", "orphan"),
            )

    def test_delete_component_cascades_decisions(self, mem_conn):
        """Deleting a component should cascade-delete its decisions."""
        mid = db.add_module(mem_conn, "M", "module", None, None, None, None)
        cid = db.add_component(mem_conn, mid, "C", None, None)
        db.add_decision(mem_conn, "tech", "Some decision", component_id=cid)
        assert len(db.get_decisions(mem_conn, component_id=cid)) == 1
        db.delete_component(mem_conn, cid)
        assert db.get_decisions(mem_conn, component_id=cid) == []


# ---------------------------------------------------------------------------
# Change records
# ---------------------------------------------------------------------------

class TestChangeRecords:
    def test_add_and_get(self, mem_conn):
        crid = db.add_change_record(
            mem_conn, "decision", 1, "edit", '{"old":"x"}', '{"new":"y"}', "human"
        )
        assert isinstance(crid, int) and crid > 0
        records = db.get_change_records(mem_conn)
        assert len(records) == 1
        assert records[0]["action"] == "edit"

    def test_get_since_baseline(self, seeded_conn):
        baseline = db.get_current_baseline(seeded_conn)
        bid = baseline["id"]

        # Add a change record tied to this baseline
        db.add_change_record(seeded_conn, "decision", 1, "edit", None, None, "human", baseline_id=bid)
        # Add one without baseline
        db.add_change_record(seeded_conn, "decision", 2, "add", None, None, "human", baseline_id=None)

        records_with_baseline = db.get_change_records(seeded_conn, since_baseline_id=bid)
        assert len(records_with_baseline) == 1

        all_records = db.get_change_records(seeded_conn)
        assert len(all_records) == 2


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------

class TestBaselines:
    def test_create_baseline(self, mem_conn):
        bid = db.create_baseline(mem_conn)
        assert isinstance(bid, int) and bid > 0

    def test_get_current_baseline(self, mem_conn):
        assert db.get_current_baseline(mem_conn) is None
        b1 = db.create_baseline(mem_conn)
        b2 = db.create_baseline(mem_conn)
        current = db.get_current_baseline(mem_conn)
        assert current["id"] == b2

    def test_seeded_has_baseline(self, seeded_conn):
        assert db.get_current_baseline(seeded_conn) is not None


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------

class TestTickets:
    def test_add_ticket(self, mem_conn):
        tid = db.add_ticket(mem_conn, "Fix bug", "Description", "Tests pass")
        assert isinstance(tid, int) and tid > 0

    def test_get_tickets(self, mem_conn):
        db.add_ticket(mem_conn, "T1", "D1", "AC1")
        db.add_ticket(mem_conn, "T2", "D2", "AC2")
        tickets = db.get_tickets(mem_conn)
        assert len(tickets) == 2

    def test_get_tickets_by_status(self, mem_conn):
        db.add_ticket(mem_conn, "T1", "D1", "AC1")
        tickets = db.get_tickets(mem_conn, status="open")
        assert len(tickets) == 1
        assert db.get_tickets(mem_conn, status="closed") == []

    def test_ticket_files(self, mem_conn):
        tid = db.add_ticket(mem_conn, "T", "D", None)
        db.add_ticket_files(mem_conn, tid, ["a.py", "b.py"])
        rows = mem_conn.execute(
            "SELECT path FROM ticket_files WHERE ticket_id = ?", (tid,)
        ).fetchall()
        assert len(rows) == 2

    def test_ticket_decisions(self, mem_conn):
        mid = db.add_module(mem_conn, "M", "module", None, None, None, None)
        did = db.add_decision(mem_conn, "tech", "Use X", module_id=mid)
        tid = db.add_ticket(mem_conn, "T", "D", None)
        db.add_ticket_decisions(mem_conn, tid, [did])
        rows = mem_conn.execute(
            "SELECT * FROM ticket_decisions WHERE ticket_id = ?", (tid,)
        ).fetchall()
        assert len(rows) == 1

    def test_ticket_change_records(self, mem_conn):
        crid = db.add_change_record(mem_conn, "decision", 1, "add", None, '{}', "human")
        tid = db.add_ticket(mem_conn, "T", "D", None)
        db.link_ticket_change_records(mem_conn, tid, [crid])
        rows = mem_conn.execute(
            "SELECT * FROM ticket_change_records WHERE ticket_id = ?", (tid,)
        ).fetchall()
        assert len(rows) == 1
