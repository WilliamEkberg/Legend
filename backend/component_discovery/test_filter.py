# Doc: Natural_Language_Code/component_discovery/info_component_discovery.md
"""
Test file detection using language-agnostic patterns.

Extracted from pipeline.py — separates source files from test files
so only source-to-source edges enter the clustering graph.
"""

import re

_TEST_PATTERNS = [
    re.compile(r'(^|/)tests?/'),           # files under test/ or tests/
    re.compile(r'(^|/)test_[^/]+\.\w+$'),  # test_*.ext anywhere
    re.compile(r'(^|/)conftest\.py$'),      # pytest conftest
    re.compile(r'(^|/)__tests__/'),         # JS/TS convention
    re.compile(r'(^|/)spec/'),             # Ruby/RSpec convention
    re.compile(r'(^|/)fixtures?/'),        # test fixtures
    re.compile(r'(^|/)mocks?/'),           # mock data
]


def is_test_file(path: str) -> bool:
    """Check if a file path matches test file patterns."""
    return any(p.search(path) for p in _TEST_PATTERNS)


def split_source_and_test(files: set[str]) -> tuple[set[str], set[str]]:
    """Split a set of file paths into source and test sets."""
    source = set()
    test = set()
    for f in files:
        if is_test_file(f):
            test.add(f)
        else:
            source.add(f)
    return source, test
