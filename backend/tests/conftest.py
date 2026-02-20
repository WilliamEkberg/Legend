"""Shared fixtures for backend tests."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

# Allow imports from backend/ root
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db


@pytest.fixture()
def mem_conn():
    """In-memory SQLite connection with schema initialized."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    db.init_schema(conn)
    yield conn
    conn.close()


@pytest.fixture()
def seeded_conn(mem_conn):
    """In-memory DB pre-populated with two modules, components, edges, and decisions."""
    conn = mem_conn

    # Pipeline run
    run_id = db.start_pipeline_run(conn, "test_seed")

    # Modules
    m1 = db.add_module(conn, "Auth Service", "module", "service", "Python", "in-repo", "k8s", run_id)
    m2 = db.add_module(conn, "Shared Utils", "shared-library", "library", "Python", "in-repo", None, run_id)

    db.add_module_directories(conn, m1, ["src/auth", "src/auth/handlers"])
    db.add_module_directories(conn, m2, ["src/utils"])

    # Components
    c1 = db.add_component(conn, m1, "Login Handler", "Handles login", 0.9, run_id)
    c2 = db.add_component(conn, m1, "Token Manager", "JWT tokens", 0.85, run_id)
    c3 = db.add_component(conn, m2, "String Helpers", "String utils", 0.95, run_id)

    db.add_component_files(conn, c1, ["src/auth/login.py", "src/auth/tests/test_login.py"], [False, True])
    db.add_component_files(conn, c2, ["src/auth/token.py"], [False])
    db.add_component_files(conn, c3, ["src/utils/strings.py"], [False])

    # Module edges
    db.add_module_edge(conn, m1, m2, "depends_on", 1.0, '{"label":"uses Shared Utils"}', run_id)

    # Component edges
    db.add_component_edge(conn, c1, c2, "depends-on", 3.0, '{"label":"calls token manager"}', run_id)

    # Decisions
    db.add_decision(conn, "pattern", "Uses JWT for auth tokens", module_id=m1, run_id=run_id)
    db.add_decision(conn, "tech", "Python 3.12+", component_id=c1, source="human", run_id=run_id)

    # Baseline
    db.create_baseline(conn, run_id)

    db.complete_pipeline_run(conn, run_id)

    yield conn


@pytest.fixture()
def tmp_db_path(tmp_path):
    """Return a path to a temporary SQLite file (initialized)."""
    db_file = tmp_path / "test.db"
    conn = db.connect(str(db_file))
    db.init_schema(conn)
    db.close(conn)
    return db_file
