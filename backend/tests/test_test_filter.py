"""Tests for test_filter.is_test_file() and split_source_and_test()."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from component_discovery.test_filter import is_test_file, split_source_and_test


class TestIsTestFile:
    # Files under test/ or tests/ directories
    @pytest.mark.parametrize("path", [
        "tests/test_auth.py",
        "src/tests/test_db.py",
        "test/unit/test_foo.py",
        "pkg/test/helpers.py",
    ])
    def test_test_directory(self, path):
        assert is_test_file(path) is True

    # test_*.ext pattern
    @pytest.mark.parametrize("path", [
        "test_main.py",
        "src/test_utils.py",
        "lib/test_parser.js",
    ])
    def test_test_prefix(self, path):
        assert is_test_file(path) is True

    # conftest.py
    @pytest.mark.parametrize("path", [
        "conftest.py",
        "src/conftest.py",
        "tests/conftest.py",
    ])
    def test_conftest(self, path):
        assert is_test_file(path) is True

    # __tests__/ directory (JS/TS convention)
    @pytest.mark.parametrize("path", [
        "__tests__/App.test.js",
        "src/__tests__/utils.test.ts",
    ])
    def test_js_tests_dir(self, path):
        assert is_test_file(path) is True

    # spec/ directory (Ruby/RSpec)
    @pytest.mark.parametrize("path", [
        "spec/models/user_spec.rb",
        "src/spec/helpers.rb",
    ])
    def test_spec_directory(self, path):
        assert is_test_file(path) is True

    # fixtures/ and fixture/
    @pytest.mark.parametrize("path", [
        "fixtures/data.json",
        "src/fixture/sample.yaml",
        "tests/fixtures/setup.py",
    ])
    def test_fixtures(self, path):
        assert is_test_file(path) is True

    # mocks/ and mock/
    @pytest.mark.parametrize("path", [
        "mocks/api.py",
        "src/mock/handler.py",
    ])
    def test_mocks(self, path):
        assert is_test_file(path) is True

    # Source files that should NOT match
    @pytest.mark.parametrize("path", [
        "src/main.py",
        "src/auth/login.py",
        "lib/utils.js",
        "app/models/user.rb",
        "setup.py",
        "index.ts",
        "src/testing_utils.py",  # "testing" doesn't match test_ prefix
        "src/contestant.py",     # "contest" doesn't match
    ])
    def test_source_files(self, path):
        assert is_test_file(path) is False


class TestSplitSourceAndTest:
    def test_basic_split(self):
        files = {"src/main.py", "tests/test_main.py", "src/utils.py"}
        source, test = split_source_and_test(files)
        assert source == {"src/main.py", "src/utils.py"}
        assert test == {"tests/test_main.py"}

    def test_all_source(self):
        files = {"src/a.py", "src/b.py"}
        source, test = split_source_and_test(files)
        assert source == files
        assert test == set()

    def test_all_test(self):
        files = {"tests/test_a.py", "tests/test_b.py"}
        source, test = split_source_and_test(files)
        assert source == set()
        assert test == files

    def test_empty(self):
        source, test = split_source_and_test(set())
        assert source == set()
        assert test == set()

    def test_mixed_patterns(self):
        files = {
            "src/main.py",
            "test_main.py",
            "src/__tests__/main.test.js",
            "src/utils.ts",
            "conftest.py",
        }
        source, test = split_source_and_test(files)
        assert source == {"src/main.py", "src/utils.ts"}
        assert len(test) == 3
