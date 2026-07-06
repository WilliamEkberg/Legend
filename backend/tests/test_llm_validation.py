"""Tests for the llm_validation boundary (structural validation + robust JSON parsing)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import llm_validation
from llm_validation import LLMOutputError


class TestValidateL2Modules:
    def test_missing_modules_key(self):
        with pytest.raises(LLMOutputError):
            llm_validation.validate_l2_modules({})

    def test_non_list_modules(self):
        with pytest.raises(LLMOutputError):
            llm_validation.validate_l2_modules({"modules": "nope"})

    def test_missing_name(self):
        with pytest.raises(LLMOutputError) as exc:
            llm_validation.validate_l2_modules({"modules": [{"id": "m1"}]})
        assert "modules[0]" in str(exc.value)
        assert "name" in str(exc.value)

    def test_missing_id(self):
        with pytest.raises(LLMOutputError) as exc:
            llm_validation.validate_l2_modules({"modules": [{"name": "Auth"}]})
        assert "id" in str(exc.value)

    def test_duplicate_names(self):
        with pytest.raises(LLMOutputError) as exc:
            llm_validation.validate_l2_modules({"modules": [
                {"id": "m1", "name": "Auth"},
                {"id": "m2", "name": "Auth"},
            ]})
        assert "duplicate name" in str(exc.value)

    def test_duplicate_ids(self):
        with pytest.raises(LLMOutputError) as exc:
            llm_validation.validate_l2_modules({"modules": [
                {"id": "m1", "name": "Auth"},
                {"id": "m1", "name": "Billing"},
            ]})
        assert "duplicate id" in str(exc.value)

    def test_non_list_directories(self):
        with pytest.raises(LLMOutputError) as exc:
            llm_validation.validate_l2_modules({"modules": [
                {"id": "m1", "name": "Auth", "directories": "src/auth"},
            ]})
        assert "directories" in str(exc.value)

    def test_happy_path_normalized(self):
        out = llm_validation.validate_l2_modules({"modules": [
            {"id": "m1", "name": "  Auth  ", "sourceOrigin": "in-repo",
             "deploymentTarget": "k8s", "type": "service", "technology": "py",
             "directories": ["src/auth"], "relationships": [{"targetModuleId": "m2"}],
             "consumedBy": ["m2"]},
            {"id": "m2", "name": "Utils"},
        ]})
        assert out[0]["name"] == "Auth"
        assert out[0]["source_origin"] == "in-repo"
        assert out[0]["deployment_target"] == "k8s"
        assert out[0]["directories"] == ["src/auth"]
        assert out[0]["relationships"] == [{"targetModuleId": "m2"}]
        assert out[1]["classification"] == "module"  # default
        assert out[1]["directories"] == []

    def test_non_string_fields_coerced_to_none(self):
        out = llm_validation.validate_l2_modules({"modules": [
            {"id": "m1", "name": "Auth", "type": 123, "technology": ["x"]},
        ]})
        assert out[0]["type"] is None
        assert out[0]["technology"] is None


class TestParseLlmJson:
    def test_plain_array(self):
        assert llm_validation.parse_llm_json('[1, 2, 3]', expect="array") == [1, 2, 3]

    def test_json_fenced(self):
        text = 'Here you go:\n```json\n[{"a": 1}]\n```'
        assert llm_validation.parse_llm_json(text, expect="array") == [{"a": 1}]

    def test_generic_fence_with_lang(self):
        text = "```\n{\"x\": 5}\n```"
        assert llm_validation.parse_llm_json(text, expect="object") == {"x": 5}

    def test_array_in_prose(self):
        text = 'Sure! The tickets are [{"title": "t"}] and that is all.'
        assert llm_validation.parse_llm_json(text, expect="array") == [{"title": "t"}]

    def test_none_raises(self):
        with pytest.raises(LLMOutputError):
            llm_validation.parse_llm_json(None, expect="array")

    def test_empty_raises(self):
        with pytest.raises(LLMOutputError):
            llm_validation.parse_llm_json("   ", expect="array")

    def test_truncated_raises(self):
        with pytest.raises(LLMOutputError):
            llm_validation.parse_llm_json('[{"title": "unclosed', expect="array")

    def test_expect_array_rejects_object(self):
        with pytest.raises(LLMOutputError):
            llm_validation.parse_llm_json('{"a": 1}', expect="array")

    def test_multiple_fences_uses_first_parseable(self):
        text = '```json\n[{"first": 1}]\n```\nand\n```json\n[{"second": 2}]\n```'
        assert llm_validation.parse_llm_json(text, expect="array") == [{"first": 1}]
