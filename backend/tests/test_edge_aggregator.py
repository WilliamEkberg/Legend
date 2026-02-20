"""Tests for edge_aggregator.aggregate_component_edges()."""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from component_discovery.edge_aggregator import aggregate_component_edges


class TestAggregateComponentEdges:
    def _make_components(self):
        return [
            {"name": "Auth", "files": ["src/auth/login.py", "src/auth/token.py"]},
            {"name": "DB", "files": ["src/db/connection.py", "src/db/queries.py"]},
            {"name": "Utils", "files": ["src/utils/helpers.py"]},
        ]

    def test_empty_edges(self):
        components = self._make_components()
        result = aggregate_component_edges(components, {}, {}, {})
        assert result == []

    def test_single_call_edge(self):
        components = self._make_components()
        call_edges = {("src/auth/login.py", "src/db/connection.py"): 3}
        result = aggregate_component_edges(components, call_edges, {}, {})
        assert len(result) == 1
        assert result[0]["edge_type"] == "call"
        assert result[0]["source"] == "Auth"
        assert result[0]["target"] == "DB"
        # Single edge is always the max → scaled to 10.0
        assert result[0]["weight"] == 10.0

    def test_same_component_edges_ignored(self):
        """Edges within the same component should not produce component edges."""
        components = self._make_components()
        call_edges = {("src/auth/login.py", "src/auth/token.py"): 5}
        result = aggregate_component_edges(components, call_edges, {}, {})
        assert result == []

    def test_separate_edge_types(self):
        """Call + import between same pair produce 2 separate edges."""
        components = self._make_components()
        call_edges = {("src/auth/login.py", "src/db/connection.py"): 2}
        import_edges = {("src/auth/login.py", "src/db/queries.py"): 1}
        result = aggregate_component_edges(components, call_edges, import_edges, {})
        assert len(result) == 2
        types = {e["edge_type"] for e in result}
        assert types == {"call", "import"}

    def test_multiple_file_edges_aggregate_per_type(self):
        """Multiple file edges of same type between same component pair should combine."""
        components = self._make_components()
        call_edges = {
            ("src/auth/login.py", "src/db/connection.py"): 2,
            ("src/auth/token.py", "src/db/queries.py"): 3,
        }
        result = aggregate_component_edges(components, call_edges, {}, {})
        assert len(result) == 1
        assert result[0]["edge_type"] == "call"
        # Single edge → scaled to 10.0
        assert result[0]["weight"] == 10.0

    def test_unknown_files_ignored(self):
        """Files not in any component should be silently skipped."""
        components = self._make_components()
        call_edges = {("unknown.py", "src/auth/login.py"): 1}
        result = aggregate_component_edges(components, call_edges, {}, {})
        assert result == []

    def test_inheritance_edges_typed(self):
        components = self._make_components()
        inheritance_edges = {("src/auth/login.py", "src/utils/helpers.py"): 1}
        result = aggregate_component_edges(components, {}, {}, inheritance_edges)
        assert len(result) == 1
        assert result[0]["edge_type"] == "inheritance"

    def test_all_three_edge_types_separate(self):
        """Three edge types between same pair produce 3 separate edges."""
        components = self._make_components()
        call_edges = {("src/auth/login.py", "src/db/connection.py"): 2}
        import_edges = {("src/auth/login.py", "src/db/connection.py"): 3}
        inherit_edges = {("src/auth/token.py", "src/db/queries.py"): 1}
        result = aggregate_component_edges(components, call_edges, import_edges, inherit_edges)
        assert len(result) == 3
        types = {e["edge_type"] for e in result}
        assert types == {"call", "import", "inheritance"}

    def test_sorted_by_weight_descending(self):
        components = self._make_components()
        call_edges = {
            ("src/auth/login.py", "src/db/connection.py"): 1,
            ("src/auth/login.py", "src/utils/helpers.py"): 10,
        }
        result = aggregate_component_edges(components, call_edges, {}, {})
        assert len(result) == 2
        assert result[0]["weight"] > result[1]["weight"]
        # Strongest edge should be 10.0
        assert result[0]["weight"] == 10.0

    def test_metadata_is_empty_dict(self):
        components = self._make_components()
        call_edges = {("src/auth/login.py", "src/db/connection.py"): 1}
        result = aggregate_component_edges(components, call_edges, {}, {})
        assert result[0]["metadata"] == {}

    def test_three_way_edges(self):
        """Three components with call edges between all pairs."""
        components = self._make_components()
        call_edges = {
            ("src/auth/login.py", "src/db/connection.py"): 1,
            ("src/auth/login.py", "src/utils/helpers.py"): 2,
            ("src/db/connection.py", "src/utils/helpers.py"): 3,
        }
        result = aggregate_component_edges(components, call_edges, {}, {})
        assert len(result) == 3
        assert all(e["edge_type"] == "call" for e in result)

    def test_weights_scaled_0_to_10(self):
        """All weights should be in the 0-10 range, with max = 10."""
        components = self._make_components()
        call_edges = {
            ("src/auth/login.py", "src/db/connection.py"): 1,
            ("src/auth/login.py", "src/utils/helpers.py"): 5,
            ("src/db/connection.py", "src/utils/helpers.py"): 10,
        }
        result = aggregate_component_edges(components, call_edges, {}, {})
        weights = [e["weight"] for e in result]
        assert max(weights) == 10.0
        assert all(0 <= w <= 10 for w in weights)

    def test_normalization_affects_relative_weights(self):
        """Components with more files should have lower weights for same raw count."""
        # Big component (25 files) vs Small (1 file)
        big_small = [
            {"name": "Big", "files": [f"f{i}.py" for i in range(25)]},
            {"name": "Small", "files": ["s1.py"]},
            {"name": "Medium", "files": ["m1.py", "m2.py"]},
        ]
        # Same raw count, different component sizes
        call_edges = {
            ("f0.py", "s1.py"): 5,     # Big->Small: normalized = 5/sqrt(25) = 1.0
            ("m1.py", "s1.py"): 5,      # Medium->Small: normalized = 5/sqrt(2) ≈ 3.54
        }
        result = aggregate_component_edges(big_small, call_edges, {}, {})
        # Medium->Small should be stronger (higher weight) because smaller components
        by_pair = {(e["source"], e["target"]): e["weight"] for e in result}
        assert by_pair[("Medium", "Small")] > by_pair[("Big", "Small")]

    def test_symmetric_normalization(self):
        """Normalization should be symmetric (A->B same as B->A weight)."""
        components = [
            {"name": "A", "files": ["a1.py", "a2.py", "a3.py"]},
            {"name": "B", "files": ["b1.py"]},
        ]
        call_a_b = {("a1.py", "b1.py"): 5}
        call_b_a = {("b1.py", "a1.py"): 5}
        result_ab = aggregate_component_edges(components, call_a_b, {}, {})
        result_ba = aggregate_component_edges(components, call_b_a, {}, {})
        assert result_ab[0]["weight"] == result_ba[0]["weight"]
