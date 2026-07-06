"""Tests for the LLM trust boundary in the revalidation package.

Workers echo decision_ids back from the LLM; we must only accept in-scope IDs
and never let re-validation rewrite a human decision or trust an LLM-echoed path.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
from revalidation.component_revalidator import _revalidate_component_worker
from revalidation.module_revalidator import _revalidate_module_worker
from revalidation import new_file_classifier


class StubClient:
    """Returns a fixed response for every query() call."""
    def __init__(self, response):
        self.response = response

    def query(self, prompt, system=None, max_tokens=None):
        return self.response


def _write_source(tmp_path, rel, content="def f():\n    return 1\n"):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


class TestComponentWorkerScope:
    def test_ignores_foreign_decision_id(self, tmp_path):
        _write_source(tmp_path, "src/login.py")
        component = {"id": 1, "name": "Login"}
        pipeline_decisions = [{"id": 1, "category": "tech", "text": "orig"}]
        client = StubClient({
            "validations": [
                {"decision_id": 999, "status": "updated", "new_text": "HACKED"},
                {"decision_id": 1, "status": "confirmed"},
            ],
            "new_decisions": [],
        })
        _, _, validations = _revalidate_component_worker(
            component, ["src/login.py"], pipeline_decisions, [], "Auth", str(tmp_path), client
        )
        ids = {v["decision_id"] for v in validations}
        assert 999 not in ids
        assert 1 in ids

    def test_human_worker_ignores_foreign_id(self, tmp_path):
        _write_source(tmp_path, "src/login.py")
        component = {"id": 1, "name": "Login"}
        human_decisions = [{"id": 5, "category": "tech", "text": "human"}]
        client = StubClient({"validations": [
            {"decision_id": 888, "status": "diverged"},
            {"decision_id": 5, "status": "implemented"},
        ]})
        _, _, validations = _revalidate_component_worker(
            component, ["src/login.py"], [], human_decisions, "Auth", str(tmp_path), client
        )
        ids = {v["decision_id"] for v in validations}
        assert 888 not in ids
        assert 5 in ids


class TestRevalidationNeverUpdatesHuman:
    def test_human_decision_text_unchanged(self, tmp_path, monkeypatch):
        # Seed DB with a module + component + a human decision.
        conn = db.connect(str(tmp_path / "t.db"))
        db.init_schema(conn)
        run = db.start_pipeline_run(conn, "seed")
        mid = db.add_module(conn, "Auth", "module", None, None, "in-repo", None, run)
        cid = db.add_component(conn, mid, "Login", "p", 0.9, run)
        db.add_component_files(conn, cid, ["src/login.py"], [False])
        hd = db.add_decision(conn, "tech", "human original", component_id=cid, source="human")
        db.complete_pipeline_run(conn, run)

        _write_source(tmp_path, "src/login.py")

        from revalidation import component_revalidator
        # Force the worker to emit a malicious 'updated' on the human decision.
        def fake_worker(component, file_paths, pipeline_decisions, human_decisions,
                        module_name, source_dir, client):
            return component, module_name, [{
                "decision_id": hd, "source": "human", "status": "updated",
                "new_text": "OVERWRITTEN", "old_text": None, "reason": None, "category": "tech",
            }]
        monkeypatch.setattr(component_revalidator, "_revalidate_component_worker", fake_worker)

        # start_validation_run requires a before_version_id; make a version.
        ver = db.create_map_version(conn, trigger="test")
        vrun = db.start_validation_run(conn, before_version_id=ver, model="stub")

        component_revalidator.revalidate_all_components(conn, str(tmp_path), StubClient({}), vrun)

        row = db.get_decision(conn, hd)
        assert row["text"] == "human original"  # never rewritten
        conn.close()


class TestModuleWorkerScope:
    def test_ignores_foreign_decision_id(self):
        module = {"id": 1, "name": "Auth"}
        component_decisions = {"Login": [{"id": 10, "category": "tech", "text": "c"}]}
        pipeline_decisions = [{"id": 1, "category": "tech", "text": "orig"}]
        client = StubClient({
            "validations": [
                {"decision_id": 777, "status": "updated", "new_text": "X"},
                {"decision_id": 1, "status": "confirmed"},
            ],
            "new_decisions": [],
        })
        _, validations = _revalidate_module_worker(
            module, component_decisions, pipeline_decisions, [], client
        )
        ids = {v["decision_id"] for v in validations}
        assert 777 not in ids
        assert 1 in ids


class TestNewFileClassifier:
    def _seed(self, tmp_path):
        conn = db.connect(str(tmp_path / "t.db"))
        db.init_schema(conn)
        run = db.start_pipeline_run(conn, "seed")
        mid = db.add_module(conn, "Auth", "module", None, None, "in-repo", None, run)
        db.add_module_directories(conn, mid, ["src/auth"])
        cid = db.add_component(conn, mid, "Login", "p", 0.9, run)
        db.add_component_files(conn, cid, ["src/auth/login.py"], [False])
        db.complete_pipeline_run(conn, run)
        return conn, mid

    def test_stores_scanned_path_not_llm_echoed(self, tmp_path):
        conn, mid = self._seed(tmp_path)
        _write_source(tmp_path, "src/auth/login.py")
        _write_source(tmp_path, "src/auth/newfile.py")  # untracked

        # LLM echoes a malicious path; we must ignore it and store the real one.
        client = StubClient({"classifications": [
            {"file": "../../etc/passwd", "existing_component": "Login"}
        ]})
        stats = new_file_classifier.classify_new_files(conn, str(tmp_path), client)

        paths = db.get_component_files(conn, db.get_components(conn, mid)[0]["id"])
        assert "src/auth/newfile.py" in paths
        assert "../../etc/passwd" not in paths
        assert stats["files_assigned"] == 1
        conn.close()

    def test_skips_empty_new_component_name(self, tmp_path):
        conn, mid = self._seed(tmp_path)
        _write_source(tmp_path, "src/auth/login.py")
        _write_source(tmp_path, "src/auth/newfile.py")

        client = StubClient({"classifications": [
            {"file": "src/auth/newfile.py", "new_component": {"name": "   ", "purpose": "p"}}
        ]})
        before = len(db.get_components(conn, mid))
        stats = new_file_classifier.classify_new_files(conn, str(tmp_path), client)
        after = len(db.get_components(conn, mid))

        assert after == before  # no component created for empty name
        assert stats["components_created"] == 0
        assert stats["orphan_files"] >= 1
        conn.close()
