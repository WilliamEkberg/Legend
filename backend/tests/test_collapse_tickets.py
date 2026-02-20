"""Tests for _collapse_changes() and _build_ticket_prompt() from main.py."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Import the private functions from main.py
from main import _collapse_changes, _build_ticket_prompt


class TestCollapseChanges:
    def test_single_record_unchanged(self):
        records = [{"entity_id": 1, "action": "add", "old_value": None, "new_value": "x"}]
        result = _collapse_changes(records)
        assert len(result) == 1
        assert result[0]["action"] == "add"

    def test_add_then_remove_cancels(self):
        records = [
            {"entity_id": 1, "action": "add", "old_value": None, "new_value": "x"},
            {"entity_id": 1, "action": "remove", "old_value": "x", "new_value": None},
        ]
        result = _collapse_changes(records)
        assert len(result) == 0

    def test_add_then_edit_becomes_net_add(self):
        records = [
            {"entity_id": 1, "action": "add", "old_value": None, "new_value": "v1"},
            {"entity_id": 1, "action": "edit", "old_value": "v1", "new_value": "v2"},
        ]
        result = _collapse_changes(records)
        assert len(result) == 1
        assert result[0]["action"] == "add"
        assert result[0]["new_value"] == "v2"

    def test_edit_then_remove_becomes_net_remove(self):
        records = [
            {"entity_id": 1, "action": "edit", "old_value": "v1", "new_value": "v2"},
            {"entity_id": 1, "action": "remove", "old_value": "v2", "new_value": None},
        ]
        result = _collapse_changes(records)
        assert len(result) == 1
        assert result[0]["action"] == "remove"
        assert result[0]["old_value"] == "v1"

    def test_multiple_edits_collapse(self):
        records = [
            {"entity_id": 1, "action": "edit", "old_value": "v1", "new_value": "v2"},
            {"entity_id": 1, "action": "edit", "old_value": "v2", "new_value": "v3"},
            {"entity_id": 1, "action": "edit", "old_value": "v3", "new_value": "v4"},
        ]
        result = _collapse_changes(records)
        assert len(result) == 1
        assert result[0]["old_value"] == "v1"
        assert result[0]["new_value"] == "v4"

    def test_edit_back_to_original_is_noop(self):
        records = [
            {"entity_id": 1, "action": "edit", "old_value": "v1", "new_value": "v2"},
            {"entity_id": 1, "action": "edit", "old_value": "v2", "new_value": "v1"},
        ]
        result = _collapse_changes(records)
        assert len(result) == 0

    def test_different_entities_not_combined(self):
        records = [
            {"entity_id": 1, "action": "add", "old_value": None, "new_value": "a"},
            {"entity_id": 2, "action": "edit", "old_value": "x", "new_value": "y"},
        ]
        result = _collapse_changes(records)
        assert len(result) == 2

    def test_empty_records(self):
        assert _collapse_changes([]) == []

    def test_add_edit_edit_remove_cancels(self):
        records = [
            {"entity_id": 1, "action": "add", "old_value": None, "new_value": "v1"},
            {"entity_id": 1, "action": "edit", "old_value": "v1", "new_value": "v2"},
            {"entity_id": 1, "action": "edit", "old_value": "v2", "new_value": "v3"},
            {"entity_id": 1, "action": "remove", "old_value": "v3", "new_value": None},
        ]
        result = _collapse_changes(records)
        assert len(result) == 0

    def test_mixed_entities(self):
        """Multiple entities with different collapse scenarios."""
        records = [
            # Entity 1: add + remove = cancel
            {"entity_id": 1, "action": "add", "old_value": None, "new_value": "a"},
            {"entity_id": 1, "action": "remove", "old_value": "a", "new_value": None},
            # Entity 2: single edit = kept
            {"entity_id": 2, "action": "edit", "old_value": "x", "new_value": "y"},
            # Entity 3: edit to same = noop
            {"entity_id": 3, "action": "edit", "old_value": "same", "new_value": "diff"},
            {"entity_id": 3, "action": "edit", "old_value": "diff", "new_value": "same"},
        ]
        result = _collapse_changes(records)
        assert len(result) == 1
        assert result[0]["entity_id"] == 2


class TestBuildTicketPrompt:
    def test_basic_structure(self):
        collapsed = [{
            "action": "add",
            "entity_id": 1,
            "old_value": None,
            "new_value": {"category": "tech", "text": "Use Redis"},
            "context": {"component_name": "Cache", "module_name": "Backend"},
        }]
        prompt = _build_ticket_prompt(collapsed)
        assert "ADD decision" in prompt
        assert '"Cache"' in prompt
        assert "Backend" in prompt
        assert "Use Redis" in prompt

    def test_edit_shows_old_and_new(self):
        collapsed = [{
            "action": "edit",
            "entity_id": 1,
            "old_value": {"category": "pattern", "text": "Old approach"},
            "new_value": {"category": "pattern", "text": "New approach"},
            "context": {"component_name": "Auth", "module_name": "Core"},
        }]
        prompt = _build_ticket_prompt(collapsed)
        assert "EDIT decision" in prompt
        assert "Was:" in prompt
        assert "Old approach" in prompt
        assert "Now:" in prompt
        assert "New approach" in prompt

    def test_remove_shows_old_only(self):
        collapsed = [{
            "action": "remove",
            "entity_id": 1,
            "old_value": {"category": "tech", "text": "Deprecated"},
            "new_value": None,
            "context": {"component_name": "Legacy", "module_name": "Old"},
        }]
        prompt = _build_ticket_prompt(collapsed)
        assert "REMOVE decision" in prompt
        assert "Was:" in prompt
        assert "Deprecated" in prompt

    def test_missing_context_uses_unknown(self):
        collapsed = [{
            "action": "add",
            "entity_id": 1,
            "old_value": None,
            "new_value": {"category": "tech", "text": "Something"},
            "context": None,
        }]
        prompt = _build_ticket_prompt(collapsed)
        assert "unknown component" in prompt
        assert "unknown module" in prompt

    def test_json_return_instructions(self):
        collapsed = [{
            "action": "add",
            "entity_id": 1,
            "old_value": None,
            "new_value": {"category": "tech", "text": "X"},
            "context": {"component_name": "A", "module_name": "B"},
        }]
        prompt = _build_ticket_prompt(collapsed)
        assert "JSON array" in prompt
        assert "is_map_correction" in prompt

    def test_multiple_changes_numbered(self):
        collapsed = [
            {
                "action": "add",
                "entity_id": 1,
                "old_value": None,
                "new_value": {"category": "tech", "text": "A"},
                "context": {"component_name": "C1", "module_name": "M1"},
            },
            {
                "action": "edit",
                "entity_id": 2,
                "old_value": {"category": "tech", "text": "B1"},
                "new_value": {"category": "tech", "text": "B2"},
                "context": {"component_name": "C2", "module_name": "M2"},
            },
        ]
        prompt = _build_ticket_prompt(collapsed)
        assert "1. ADD" in prompt
        assert "2. EDIT" in prompt
