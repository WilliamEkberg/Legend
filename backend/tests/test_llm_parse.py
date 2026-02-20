"""Tests for _parse_json_response() from both llm_client.py copies."""

import pytest
import sys
from pathlib import Path

# Add backend root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from component_discovery.llm_client import LLMClient as CDClient
from map_descriptions.llm_client import LLMClient as MDClient


# Parametrize to test both identical copies
@pytest.fixture(params=[CDClient, MDClient], ids=["component_discovery", "map_descriptions"])
def parser(request):
    """Return a _parse_json_response bound method from an LLMClient instance."""
    client = request.param()
    return client._parse_json_response


class TestParseJsonResponse:
    def test_plain_json(self, parser):
        result = parser('{"key": "value", "num": 42}')
        assert result == {"key": "value", "num": 42}

    def test_json_with_whitespace(self, parser):
        result = parser('  \n  {"key": "value"}  \n  ')
        assert result == {"key": "value"}

    def test_json_code_block(self, parser):
        text = '```json\n{"key": "value"}\n```'
        result = parser(text)
        assert result == {"key": "value"}

    def test_generic_code_block(self, parser):
        text = '```\n{"key": "value"}\n```'
        result = parser(text)
        assert result == {"key": "value"}

    def test_code_block_with_language_tag(self, parser):
        text = '```javascript\n{"key": "value"}\n```'
        result = parser(text)
        assert result == {"key": "value"}

    def test_json_surrounded_by_prose(self, parser):
        text = 'Here is the result:\n{"key": "value"}\nHope this helps!'
        result = parser(text)
        assert result == {"key": "value"}

    def test_nested_json(self, parser):
        text = '{"outer": {"inner": [1, 2, 3]}}'
        result = parser(text)
        assert result == {"outer": {"inner": [1, 2, 3]}}

    def test_json_code_block_with_prose(self, parser):
        text = 'Here is the JSON:\n```json\n{"components": [{"name": "A"}]}\n```\nDone.'
        result = parser(text)
        assert result == {"components": [{"name": "A"}]}

    def test_invalid_json_raises(self, parser):
        with pytest.raises(ValueError, match="Could not parse JSON"):
            parser("this is not json at all")

    def test_empty_braces_still_valid(self, parser):
        result = parser("{}")
        assert result == {}

    def test_json_array_at_top_level(self, parser):
        """Arrays should also parse (json.loads supports them)."""
        result = parser('[{"a": 1}, {"b": 2}]')
        assert result == [{"a": 1}, {"b": 2}]

    def test_multiple_code_blocks_uses_first(self, parser):
        text = '```json\n{"first": true}\n```\n\n```json\n{"second": true}\n```'
        result = parser(text)
        assert result == {"first": True}

    def test_curly_brace_extraction_with_noise(self, parser):
        text = 'The answer is {"result": "ok"} and that is all.'
        result = parser(text)
        assert result == {"result": "ok"}


class TestEstimateTokens:
    def test_component_discovery(self):
        from component_discovery.llm_client import estimate_tokens
        assert estimate_tokens("abcd") == 1
        assert estimate_tokens("abcdefgh") == 2
        assert estimate_tokens("") == 0

    def test_map_descriptions(self):
        from map_descriptions.llm_client import estimate_tokens
        assert estimate_tokens("abcd") == 1
        assert estimate_tokens("a" * 400) == 100
