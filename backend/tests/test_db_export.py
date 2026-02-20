"""Tests for db.export_full_map() nested JSON output."""

import json

import db


class TestExportFullMap:
    def test_empty_db(self, mem_conn):
        result = db.export_full_map(mem_conn)
        assert result == {"modules": [], "module_edges": [], "component_edges": []}

    def test_structure_keys(self, seeded_conn):
        result = db.export_full_map(seeded_conn)
        assert set(result.keys()) == {"modules", "module_edges", "component_edges"}

    def test_modules_present(self, seeded_conn):
        result = db.export_full_map(seeded_conn)
        names = {m["name"] for m in result["modules"]}
        assert "Auth Service" in names
        assert "Shared Utils" in names

    def test_module_has_directories(self, seeded_conn):
        result = db.export_full_map(seeded_conn)
        auth = [m for m in result["modules"] if m["name"] == "Auth Service"][0]
        assert "directories" in auth
        assert "src/auth" in auth["directories"]

    def test_module_has_decisions(self, seeded_conn):
        result = db.export_full_map(seeded_conn)
        auth = [m for m in result["modules"] if m["name"] == "Auth Service"][0]
        assert "decisions" in auth
        assert len(auth["decisions"]) == 1
        assert "JWT" in auth["decisions"][0]["text"]

    def test_module_has_components(self, seeded_conn):
        result = db.export_full_map(seeded_conn)
        auth = [m for m in result["modules"] if m["name"] == "Auth Service"][0]
        assert "components" in auth
        comp_names = {c["name"] for c in auth["components"]}
        assert "Login Handler" in comp_names
        assert "Token Manager" in comp_names

    def test_component_has_files(self, seeded_conn):
        result = db.export_full_map(seeded_conn)
        auth = [m for m in result["modules"] if m["name"] == "Auth Service"][0]
        login = [c for c in auth["components"] if c["name"] == "Login Handler"][0]
        assert "files" in login
        assert len(login["files"]) == 2
        # Files should have path and is_test
        for f in login["files"]:
            assert "path" in f
            assert "is_test" in f

    def test_component_has_decisions(self, seeded_conn):
        result = db.export_full_map(seeded_conn)
        auth = [m for m in result["modules"] if m["name"] == "Auth Service"][0]
        login = [c for c in auth["components"] if c["name"] == "Login Handler"][0]
        assert "decisions" in login
        assert len(login["decisions"]) == 1

    def test_module_edges(self, seeded_conn):
        result = db.export_full_map(seeded_conn)
        assert len(result["module_edges"]) == 1
        edge = result["module_edges"][0]
        assert "source_id" in edge
        assert "target_id" in edge
        assert "weight" in edge

    def test_module_edge_metadata_parsed(self, seeded_conn):
        result = db.export_full_map(seeded_conn)
        edge = result["module_edges"][0]
        # Metadata should be parsed from JSON string to dict
        assert isinstance(edge["metadata"], dict)
        assert edge["metadata"].get("label") == "uses Shared Utils"

    def test_component_edges(self, seeded_conn):
        result = db.export_full_map(seeded_conn)
        assert len(result["component_edges"]) == 1
        edge = result["component_edges"][0]
        assert isinstance(edge["metadata"], dict)

    def test_edge_metadata_invalid_json_becomes_empty_dict(self, mem_conn):
        """If metadata is invalid JSON, it should become {}."""
        m1 = db.add_module(mem_conn, "A", "module", None, None, None, None)
        m2 = db.add_module(mem_conn, "B", "module", None, None, None, None)
        db.add_module_edge(mem_conn, m1, m2, "depends_on", 1.0, "not valid json")
        result = db.export_full_map(mem_conn)
        assert result["module_edges"][0]["metadata"] == {}

    def test_edge_metadata_null_becomes_empty_dict(self, mem_conn):
        """If metadata is NULL, it should become {}."""
        m1 = db.add_module(mem_conn, "A", "module", None, None, None, None)
        m2 = db.add_module(mem_conn, "B", "module", None, None, None, None)
        db.add_module_edge(mem_conn, m1, m2, "depends_on", 1.0, None)
        result = db.export_full_map(mem_conn)
        assert result["module_edges"][0]["metadata"] == {}

    def test_is_test_boolean(self, seeded_conn):
        """is_test should be a Python bool, not int."""
        result = db.export_full_map(seeded_conn)
        auth = [m for m in result["modules"] if m["name"] == "Auth Service"][0]
        login = [c for c in auth["components"] if c["name"] == "Login Handler"][0]
        test_file = [f for f in login["files"] if "test_" in f["path"]][0]
        assert test_file["is_test"] is True
        source_file = [f for f in login["files"] if "test_" not in f["path"]][0]
        assert source_file["is_test"] is False
