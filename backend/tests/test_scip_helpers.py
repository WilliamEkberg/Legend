"""Tests for pure helper functions in scip_filter.py.

We do NOT test parse_scip_for_module (requires real .scip files).
We test the utility functions that don't depend on protobuf data.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from component_discovery.scip_filter import (
    _is_local_symbol,
    _is_definition,
    _is_import,
    _is_callable_by_kind,
    _is_callable_by_suffix,
    _file_in_module,
    _CALLABLE_KINDS,
)


class TestIsLocalSymbol:
    def test_local(self):
        assert _is_local_symbol("local 42") is True

    def test_local_with_space(self):
        assert _is_local_symbol("local ") is True

    def test_not_local(self):
        assert _is_local_symbol("scip-python python 3.12 mymod/`MyClass`#") is False

    def test_empty(self):
        assert _is_local_symbol("") is False


class TestIsDefinition:
    def test_definition_bit_set(self):
        assert _is_definition(0x1) is True
        assert _is_definition(0x3) is True  # definition + import
        assert _is_definition(0x5) is True  # definition + other

    def test_definition_bit_not_set(self):
        assert _is_definition(0x0) is False
        assert _is_definition(0x2) is False
        assert _is_definition(0x4) is False


class TestIsImport:
    def test_import_bit_set(self):
        assert _is_import(0x2) is True
        assert _is_import(0x3) is True  # definition + import

    def test_import_bit_not_set(self):
        assert _is_import(0x0) is False
        assert _is_import(0x1) is False
        assert _is_import(0x4) is False


class TestIsCallableByKind:
    def test_known_callable_kinds(self):
        for kind in _CALLABLE_KINDS:
            assert _is_callable_by_kind(kind) is True

    def test_non_callable_kinds(self):
        assert _is_callable_by_kind(0) is False
        assert _is_callable_by_kind(1) is False  # File
        assert _is_callable_by_kind(5) is False   # Class
        assert _is_callable_by_kind(999) is False


class TestIsCallableBySuffix:
    def test_method_suffix(self):
        assert _is_callable_by_suffix("scip-python python 3.12 mymod/`MyClass`#`method`().") is True

    def test_function_suffix_with_params(self):
        assert _is_callable_by_suffix("scip-python python 3.12 mymod/`func`(a, b).") is True

    def test_no_suffix(self):
        assert _is_callable_by_suffix("scip-python python 3.12 mymod/`MY_CONST`.") is False

    def test_empty(self):
        assert _is_callable_by_suffix("") is False

    def test_parentheses_without_dot(self):
        assert _is_callable_by_suffix("something()") is False


class TestFileInModule:
    def test_direct_match(self):
        assert _file_in_module("src/auth/login.py", ["src/auth"]) is True

    def test_nested_file(self):
        assert _file_in_module("src/auth/handlers/oauth.py", ["src/auth"]) is True

    def test_no_match(self):
        assert _file_in_module("src/db/models.py", ["src/auth"]) is False

    def test_multiple_prefixes(self):
        assert _file_in_module("src/utils/helpers.py", ["src/auth", "src/utils"]) is True

    def test_prefix_with_trailing_slash(self):
        assert _file_in_module("src/auth/login.py", ["src/auth/"]) is True

    def test_partial_name_no_match(self):
        """'src/authorize' should not match prefix 'src/auth'."""
        # Actually it WILL match because 'src/authorize/x.py'.startswith('src/auth/') is False
        # but 'src/auth' + '/' = 'src/auth/' and 'src/authorize/x.py'.startswith('src/auth/') is False
        assert _file_in_module("src/authorize/x.py", ["src/auth"]) is False

    def test_exact_directory_match(self):
        """File path equals the prefix exactly (no trailing slash)."""
        assert _file_in_module("src/auth", ["src/auth"]) is True

    def test_empty_prefixes(self):
        assert _file_in_module("src/auth/login.py", []) is False
