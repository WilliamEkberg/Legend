"""Tests for FastAPI endpoints using TestClient.

Uses a temp DB file since endpoints check DB_PATH.exists().
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db


@pytest.fixture()
def client(tmp_path):
    """Create a TestClient with DB_PATH pointed at a temp file."""
    from fastapi.testclient import TestClient

    db_file = tmp_path / "test.db"

    # Patch DB_PATH before importing/using the app
    import main
    original_db_path = main.DB_PATH
    main.DB_PATH = db_file

    # Initialize DB
    conn = db.connect(str(db_file))
    db.init_schema(conn)
    db.close(conn)

    tc = TestClient(main.app)
    yield tc

    main.DB_PATH = original_db_path


@pytest.fixture()
def seeded_client(tmp_path):
    """TestClient with a seeded DB (modules, components, decisions, baseline)."""
    from fastapi.testclient import TestClient

    db_file = tmp_path / "test.db"

    import main
    original_db_path = main.DB_PATH
    main.DB_PATH = db_file

    conn = db.connect(str(db_file))
    db.init_schema(conn)

    run_id = db.start_pipeline_run(conn, "seed")
    m1 = db.add_module(conn, "Auth", "module", "service", "Python", "in-repo", "k8s", run_id)
    c1 = db.add_component(conn, m1, "Login", "Login handler", 0.9, run_id)
    db.add_component_files(conn, c1, ["src/login.py"], [False])
    db.add_decision(conn, "pattern", "Use JWT", module_id=m1, run_id=run_id)
    d2 = db.add_decision(conn, "tech", "Python 3.12", component_id=c1, source="human", run_id=run_id)
    db.create_baseline(conn, run_id)
    db.complete_pipeline_run(conn, run_id)
    db.close(conn)

    tc = TestClient(main.app)
    yield tc, db_file, d2

    main.DB_PATH = original_db_path


# ---------------------------------------------------------------------------
# /api/map
# ---------------------------------------------------------------------------

class TestMapEndpoint:
    def test_empty_db(self, client):
        resp = client.get("/api/map")
        assert resp.status_code == 200
        data = resp.json()
        assert data["modules"] == []
        assert data["module_edges"] == []
        assert data["component_edges"] == []

    def test_seeded_db(self, seeded_client):
        client, _, _ = seeded_client
        resp = client.get("/api/map")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["modules"]) == 1
        assert data["modules"][0]["name"] == "Auth"

    def test_no_db_file(self, tmp_path):
        """When DB file doesn't exist, return empty map."""
        from fastapi.testclient import TestClient
        import main
        original = main.DB_PATH
        main.DB_PATH = tmp_path / "nonexistent.db"

        tc = TestClient(main.app)
        resp = tc.get("/api/map")
        assert resp.status_code == 200
        assert resp.json()["modules"] == []

        main.DB_PATH = original


# ---------------------------------------------------------------------------
# Decision CRUD
# ---------------------------------------------------------------------------

class TestDecisionCRUD:
    def test_create_decision(self, seeded_client):
        client, db_file, _ = seeded_client

        # Get module ID
        conn = db.connect(str(db_file))
        mods = db.get_modules(conn)
        mid = mods[0]["id"]
        db.close(conn)

        resp = client.post("/api/decisions", json={
            "text": "New decision",
            "category": "tech",
            "module_id": mid,
        })
        assert resp.status_code == 200
        assert "id" in resp.json()

    def test_create_decision_with_component_id(self, seeded_client):
        client, db_file, _ = seeded_client

        conn = db.connect(str(db_file))
        comps = db.get_components(conn)
        cid = comps[0]["id"]
        db.close(conn)

        resp = client.post("/api/decisions", json={
            "text": "Component decision",
            "category": "pattern",
            "component_id": cid,
        })
        assert resp.status_code == 200
        assert "id" in resp.json()

    def test_create_decision_creates_change_record(self, seeded_client):
        client, db_file, _ = seeded_client

        conn = db.connect(str(db_file))
        mods = db.get_modules(conn)
        mid = mods[0]["id"]
        db.close(conn)

        client.post("/api/decisions", json={
            "text": "Tracked decision",
            "category": "pattern",
            "module_id": mid,
        })

        resp = client.get("/api/change-records")
        assert resp.status_code == 200
        records = resp.json()["records"]
        assert len(records) == 1
        assert records[0]["action"] == "add"

    def test_patch_decision(self, seeded_client):
        client, db_file, d_id = seeded_client

        resp = client.patch(f"/api/decisions/{d_id}", json={
            "text": "Updated text",
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify the update
        conn = db.connect(str(db_file))
        d = db.get_decision(conn, d_id)
        db.close(conn)
        assert d["text"] == "Updated text"
        assert d["source"] == "human"

    def test_patch_decision_both_fields(self, seeded_client):
        client, db_file, d_id = seeded_client

        resp = client.patch(f"/api/decisions/{d_id}", json={
            "text": "New text",
            "category": "boundaries",
        })
        assert resp.status_code == 200

        conn = db.connect(str(db_file))
        d = db.get_decision(conn, d_id)
        db.close(conn)
        assert d["text"] == "New text"
        assert d["category"] == "boundaries"

    def test_patch_decision_not_found(self, seeded_client):
        client, _, _ = seeded_client
        resp = client.patch("/api/decisions/99999", json={"text": "x"})
        assert resp.status_code == 404

    def test_delete_decision(self, seeded_client):
        client, db_file, d_id = seeded_client

        resp = client.delete(f"/api/decisions/{d_id}")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify deleted
        conn = db.connect(str(db_file))
        assert db.get_decision(conn, d_id) is None
        db.close(conn)

    def test_delete_decision_creates_change_record(self, seeded_client):
        client, _, d_id = seeded_client

        client.delete(f"/api/decisions/{d_id}")

        resp = client.get("/api/change-records")
        records = resp.json()["records"]
        assert any(r["action"] == "remove" for r in records)

    def test_delete_decision_not_found(self, seeded_client):
        client, _, _ = seeded_client
        resp = client.delete("/api/decisions/99999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /api/change-records
# ---------------------------------------------------------------------------

class TestChangeRecords:
    def test_empty(self, client):
        resp = client.get("/api/change-records")
        assert resp.status_code == 200
        data = resp.json()
        assert data["records"] == []

    def test_no_db(self, tmp_path):
        from fastapi.testclient import TestClient
        import main
        original = main.DB_PATH
        main.DB_PATH = tmp_path / "nonexistent.db"

        tc = TestClient(main.app)
        resp = tc.get("/api/change-records")
        assert resp.status_code == 200
        assert resp.json()["records"] == []

        main.DB_PATH = original


# ---------------------------------------------------------------------------
# /api/tickets
# ---------------------------------------------------------------------------

class TestTicketsEndpoint:
    def test_empty(self, client):
        resp = client.get("/api/tickets")
        assert resp.status_code == 200
        assert resp.json()["tickets"] == []

    def test_no_db(self, tmp_path):
        from fastapi.testclient import TestClient
        import main
        original = main.DB_PATH
        main.DB_PATH = tmp_path / "nonexistent.db"

        tc = TestClient(main.app)
        resp = tc.get("/api/tickets")
        assert resp.status_code == 200
        assert resp.json()["tickets"] == []

        main.DB_PATH = original

    def test_with_tickets(self, seeded_client):
        client, db_file, _ = seeded_client

        conn = db.connect(str(db_file))
        tid = db.add_ticket(conn, "Fix bug", "Description", "Tests pass")
        db.add_ticket_files(conn, tid, ["src/login.py"])
        db.close(conn)

        resp = client.get("/api/tickets")
        data = resp.json()
        assert len(data["tickets"]) == 1
        assert data["tickets"][0]["title"] == "Fix bug"
        assert "src/login.py" in data["tickets"][0]["files"]
