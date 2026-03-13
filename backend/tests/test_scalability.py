"""Tests for Sprint 1 scalability improvements: S1-S4."""

import math
import sys
from collections import defaultdict
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import networkx as nx
from component_discovery.graph_builder import (
    CALL_WEIGHT,
    DIRECTORY_WEIGHT_BASE,
    IMPORT_WEIGHT,
    NOISE_THRESHOLD,
    STAR_TOPOLOGY_THRESHOLD,
    add_directory_affinity,
    build_file_graph,
    strip_virtual_nodes,
)
from component_discovery.leiden_cluster import run_leiden, run_leiden_targeted
from component_discovery.partitioner import partition_files


# =========================================================================
# S1: Star topology for directory affinity
# =========================================================================

class TestStarTopology:
    def test_small_dir_uses_all_pairs(self):
        """Dirs with <= STAR_TOPOLOGY_THRESHOLD files use all-pairs edges."""
        graph = nx.Graph()
        files = [f"src/{i}.py" for i in range(5)]
        for f in files:
            graph.add_node(f)

        graph, virtual = add_directory_affinity(graph)

        assert len(virtual) == 0
        # 5 files = C(5,2) = 10 all-pairs edges
        assert graph.number_of_edges() == 10

    def test_large_dir_uses_star_topology(self):
        """Dirs with > STAR_TOPOLOGY_THRESHOLD files use star topology."""
        graph = nx.Graph()
        n = 100
        files = [f"src/models/{i}.py" for i in range(n)]
        for f in files:
            graph.add_node(f)

        graph, virtual = add_directory_affinity(graph)

        # Should create exactly 1 virtual node
        assert len(virtual) == 1
        centroid = list(virtual)[0]
        assert centroid == "__dir__/src/models"
        assert centroid in graph.nodes()

        # Star topology: n edges (one per file to centroid), not C(n,2)
        assert graph.number_of_edges() == n
        # All-pairs would be 4950 edges — confirm massive reduction
        assert n < n * (n - 1) // 2

    def test_star_weight_scales_with_log(self):
        """Star edge weight = DIRECTORY_WEIGHT_BASE / log2(n)."""
        graph = nx.Graph()
        n = 64
        files = [f"pkg/{i}.go" for i in range(n)]
        for f in files:
            graph.add_node(f)

        graph, virtual = add_directory_affinity(graph)

        centroid = list(virtual)[0]
        expected_weight = DIRECTORY_WEIGHT_BASE / math.log2(n)
        # Check weight on any edge to centroid
        assert graph[files[0]][centroid]["weight"] == pytest.approx(expected_weight)

    def test_virtual_nodes_stripped_from_clusters(self):
        """strip_virtual_nodes removes virtual nodes from cluster dict."""
        clusters = {
            "src/a.py": 0,
            "src/b.py": 0,
            "__dir__/src": 0,
            "lib/c.py": 1,
            "__dir__/lib": 1,
        }
        virtual = {"__dir__/src", "__dir__/lib"}

        result = strip_virtual_nodes(clusters, virtual)

        assert "__dir__/src" not in result
        assert "__dir__/lib" not in result
        assert len(result) == 3
        assert result["src/a.py"] == 0
        assert result["lib/c.py"] == 1

    def test_mixed_dirs_small_and_large(self):
        """Mix of small (all-pairs) and large (star) directories."""
        graph = nx.Graph()
        small_files = [f"small/{i}.py" for i in range(3)]
        large_files = [f"large/{i}.py" for i in range(20)]
        for f in small_files + large_files:
            graph.add_node(f)

        graph, virtual = add_directory_affinity(graph)

        # Only large dir should get a virtual node
        assert len(virtual) == 1
        assert "__dir__/large" in virtual

        # Small dir: C(3,2)=3 edges; Large dir: 20 star edges
        assert graph.number_of_edges() == 3 + 20

    def test_threshold_boundary(self):
        """Exactly STAR_TOPOLOGY_THRESHOLD files uses all-pairs."""
        graph = nx.Graph()
        files = [f"dir/{i}.py" for i in range(STAR_TOPOLOGY_THRESHOLD)]
        for f in files:
            graph.add_node(f)

        graph, virtual = add_directory_affinity(graph)

        assert len(virtual) == 0  # All-pairs at threshold

    def test_threshold_plus_one_uses_star(self):
        """STAR_TOPOLOGY_THRESHOLD + 1 files switches to star."""
        graph = nx.Graph()
        n = STAR_TOPOLOGY_THRESHOLD + 1
        files = [f"dir/{i}.py" for i in range(n)]
        for f in files:
            graph.add_node(f)

        graph, virtual = add_directory_affinity(graph)

        assert len(virtual) == 1
        assert graph.number_of_edges() == n


# =========================================================================
# S3: Logarithmic weighting + noise filtering
# =========================================================================

class TestLogWeightAndNoise:
    def _make_parsed(self, call_edges=None, import_edges=None, inheritance_edges=None):
        return {
            "call_edges": call_edges or {},
            "import_edges": import_edges or {},
            "inheritance_edges": inheritance_edges or {},
        }

    def test_log_weight_single_call(self):
        """A single call (count=1) gets weight log2(2)*1.0 = 1.0."""
        parsed = self._make_parsed(call_edges={("a.py", "b.py"): 1})
        graph = build_file_graph(parsed, {"a.py", "b.py"})

        assert graph.has_edge("a.py", "b.py")
        expected = math.log2(1 + 1) * CALL_WEIGHT  # log2(2) * 1.0 = 1.0
        assert graph["a.py"]["b.py"]["weight"] == pytest.approx(expected)

    def test_log_weight_high_count_compressed(self):
        """100 calls gets log2(101)*1.0 ≈ 6.66, not 100."""
        parsed = self._make_parsed(call_edges={("a.py", "b.py"): 100})
        graph = build_file_graph(parsed, {"a.py", "b.py"})

        weight = graph["a.py"]["b.py"]["weight"]
        expected = math.log2(101) * CALL_WEIGHT
        assert weight == pytest.approx(expected)
        # Much less than linear 100*1.0
        assert weight < 10

    def test_log_ratio_100_to_1(self):
        """count=100 is ~6.6x stronger than count=1 (not 100x)."""
        parsed_100 = self._make_parsed(call_edges={("a.py", "b.py"): 100})
        parsed_1 = self._make_parsed(call_edges={("a.py", "b.py"): 1})

        g100 = build_file_graph(parsed_100, {"a.py", "b.py"})
        g1 = build_file_graph(parsed_1, {"a.py", "b.py"})

        ratio = g100["a.py"]["b.py"]["weight"] / g1["a.py"]["b.py"]["weight"]
        assert 6 < ratio < 8  # log2(101)/log2(2) ≈ 6.66

    def test_noise_filter_drops_weak_edge(self):
        """Weak edge between low-degree nodes is filtered out."""
        # Create a scenario where a.py->b.py is weak (1 import = log2(2)*0.3 = 0.3)
        # and both have low degree. Also add stronger edges so median degree > 0.
        files = {"a.py", "b.py", "c.py", "d.py", "e.py"}
        parsed = self._make_parsed(
            call_edges={
                ("c.py", "d.py"): 10,
                ("c.py", "e.py"): 10,
                ("d.py", "e.py"): 10,
            },
            import_edges={
                ("a.py", "b.py"): 1,  # log2(2)*0.3 = 0.3, below NOISE_THRESHOLD
            },
        )
        graph = build_file_graph(parsed, files)

        # The weak import edge (0.3) should be dropped
        assert not graph.has_edge("a.py", "b.py")
        # Strong edges preserved
        assert graph.has_edge("c.py", "d.py")

    def test_noise_filter_keeps_strong_edge(self):
        """Strong edges are never filtered regardless of degree."""
        files = {"a.py", "b.py"}
        parsed = self._make_parsed(call_edges={("a.py", "b.py"): 5})
        graph = build_file_graph(parsed, files)

        # log2(6)*1.0 ≈ 2.58, well above NOISE_THRESHOLD
        assert graph.has_edge("a.py", "b.py")

    def test_noise_filter_preserves_high_degree_weak_edge(self):
        """Weak edge where one endpoint has high degree is preserved."""
        # Hub node c.py connects to many files
        files = {f"{i}.py" for i in range(20)} | {"hub.py", "leaf.py"}
        call_edges = {("hub.py", f"{i}.py"): 5 for i in range(20)}
        # Weak edge but hub.py is high-degree
        import_edges = {("hub.py", "leaf.py"): 1}  # 0.3 weight
        parsed = self._make_parsed(call_edges=call_edges, import_edges=import_edges)

        graph = build_file_graph(parsed, files)

        # hub.py has degree >> median, so the edge should be preserved
        assert graph.has_edge("hub.py", "leaf.py")

    def test_combined_weight_types(self):
        """Call + import + inheritance all use log weighting."""
        files = {"a.py", "b.py"}
        parsed = self._make_parsed(
            call_edges={("a.py", "b.py"): 3},
            import_edges={("a.py", "b.py"): 2},
            inheritance_edges={("a.py", "b.py"): 1},
        )
        graph = build_file_graph(parsed, files)

        expected = (
            math.log2(1 + 3) * CALL_WEIGHT
            + math.log2(1 + 2) * IMPORT_WEIGHT
            + math.log2(1 + 1) * 0.5
        )
        assert graph["a.py"]["b.py"]["weight"] == pytest.approx(expected)


# =========================================================================
# S4: Targeted resolution sweep
# =========================================================================

class TestTargetedResolution:
    def _make_clusterable_graph(self, n_groups=5, group_size=20):
        """Create a graph with clear cluster structure."""
        graph = nx.Graph()
        for g in range(n_groups):
            nodes = [f"g{g}/f{i}.py" for i in range(group_size)]
            for n in nodes:
                graph.add_node(n)
            # Dense intra-group edges
            for i, a in enumerate(nodes):
                for b in nodes[i + 1:]:
                    graph.add_edge(a, b, weight=5.0)
            # Sparse inter-group edges
            if g > 0:
                graph.add_edge(f"g{g}/f0.py", f"g{g-1}/f0.py", weight=0.5)
        return graph

    def test_converges_to_target(self):
        """Resolution sweep should produce approximately the target cluster count."""
        graph = self._make_clusterable_graph(n_groups=5, group_size=20)

        result = run_leiden_targeted(graph, target_components=5)

        n_clusters = len(set(result.values()))
        assert abs(n_clusters - 5) <= 2  # Within tolerance

    def test_returns_all_nodes(self):
        """All graph nodes should appear in the result."""
        graph = self._make_clusterable_graph(n_groups=3, group_size=10)

        result = run_leiden_targeted(graph, target_components=3)

        assert set(result.keys()) == set(graph.nodes())

    def test_empty_graph(self):
        """Empty graph returns empty dict."""
        result = run_leiden_targeted(nx.Graph(), target_components=5)
        assert result == {}

    def test_single_node(self):
        """Single-node graph returns single cluster."""
        graph = nx.Graph()
        graph.add_node("only.py")
        result = run_leiden_targeted(graph, target_components=1)
        assert result == {"only.py": 0}

    def test_target_one_puts_all_together(self):
        """Target=1 should group everything into one cluster."""
        graph = self._make_clusterable_graph(n_groups=3, group_size=5)
        result = run_leiden_targeted(graph, target_components=1)
        assert len(set(result.values())) == 1

    def test_higher_target_more_clusters(self):
        """Higher target should produce more clusters than lower target."""
        graph = self._make_clusterable_graph(n_groups=10, group_size=15)

        low = run_leiden_targeted(graph, target_components=3)
        high = run_leiden_targeted(graph, target_components=10)

        assert len(set(high.values())) >= len(set(low.values()))

    def test_respects_max_iterations(self):
        """Should complete within max_iterations (doesn't hang)."""
        graph = self._make_clusterable_graph(n_groups=5, group_size=10)
        # This should finish quickly regardless of convergence
        result = run_leiden_targeted(graph, target_components=5, max_iterations=2)
        assert len(result) == 50  # All nodes present

    def test_original_run_leiden_still_works(self):
        """Original run_leiden function unchanged."""
        graph = self._make_clusterable_graph(n_groups=3, group_size=10)
        result = run_leiden(graph, resolution=1.5)
        assert len(result) == 30


# =========================================================================
# S2: Directory-based partitioning
# =========================================================================

class TestPartitioning:
    def test_django_style_partitioning(self):
        """Django-like structure partitions into app groups."""
        files = set()
        for app in ["auth", "billing", "users", "notifications", "analytics"]:
            for f in ["models.py", "views.py", "serializers.py", "admin.py", "urls.py", "tests.py"]:
                files.add(f"apps/{app}/{f}")

        partitions = partition_files(files)

        # Should create 5 partitions (one per app)
        assert len(partitions) == 5
        # Each partition should have 6 files
        for p in partitions:
            assert len(p) == 6

    def test_monorepo_partitioning(self):
        """Monorepo with packages partitions correctly."""
        files = set()
        for pkg in ["api", "web", "mobile", "shared"]:
            for i in range(10):
                files.add(f"packages/{pkg}/src/file{i}.ts")

        partitions = partition_files(files)

        assert len(partitions) == 4
        for p in partitions:
            assert len(p) == 10

    def test_small_groups_merged_to_remainder(self):
        """Groups below min_partition_size are merged."""
        files = set()
        # 1 big group
        for i in range(20):
            files.add(f"src/main/file{i}.py")
        # 3 small groups (below default threshold of 5)
        files.add("docs/readme.py")
        files.add("scripts/build.py")
        files.add("tools/lint.py")

        partitions = partition_files(files)

        # Big group + remainder merged into smallest
        assert len(partitions) >= 1
        # All files accounted for
        all_files = set()
        for p in partitions:
            all_files.update(p)
        assert all_files == files

    def test_flat_repo_single_partition(self):
        """Flat repo (all files at root) returns single partition."""
        files = {f"file{i}.py" for i in range(10)}

        partitions = partition_files(files)

        assert len(partitions) == 1
        assert partitions[0] == files

    def test_empty_input(self):
        """Empty file set returns empty list."""
        assert partition_files(set()) == []

    def test_custom_min_partition_size(self):
        """Custom min_partition_size changes grouping."""
        files = set()
        for d in ["a", "b", "c"]:
            for i in range(3):
                files.add(f"pkg/{d}/f{i}.py")

        # With min=3, all groups are viable
        partitions = partition_files(files, min_partition_size=3)
        assert len(partitions) == 3

        # With min=5, all groups are too small → single partition
        partitions = partition_files(files, min_partition_size=5)
        assert len(partitions) == 1

    def test_depth2_grouping(self):
        """Files are grouped by first 2 directory levels."""
        files = {
            "apps/auth/models/user.py",
            "apps/auth/models/role.py",
            "apps/auth/views/login.py",
            "apps/billing/models/invoice.py",
            "apps/billing/views/payment.py",
        }

        partitions = partition_files(files, min_partition_size=2)

        # Should group by apps/auth and apps/billing
        assert len(partitions) == 2
        sizes = sorted([len(p) for p in partitions])
        assert sizes == [2, 3]

    def test_all_files_preserved(self):
        """No files lost during partitioning."""
        files = set()
        for d in ["a", "b", "c", "d"]:
            for i in range(7):
                files.add(f"src/{d}/f{i}.py")
        files.add("root.py")  # orphan

        partitions = partition_files(files)

        all_partitioned = set()
        for p in partitions:
            all_partitioned.update(p)
        assert all_partitioned == files
