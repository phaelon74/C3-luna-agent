"""Tests for agent context budgeting (compression integration)."""

from __future__ import annotations

import openai
import pytest

from mose.agent import _is_context_length_error
from mose.context_compress import (
    estimate_tokens,
    estimate_tools_tokens,
    max_input_tokens,
)


class TestTokenEstimation:
    def test_tool_messages_estimate_higher_than_prose(self):
        prose = [{"role": "user", "content": "x" * 3000}]
        tool = [{"role": "tool", "tool_call_id": "1", "content": "x" * 3000}]
        assert estimate_tokens(tool) > estimate_tokens(prose)

    def test_tools_schema_budget(self):
        tools = [{"type": "function", "function": {"name": "a", "parameters": {"x": 1}}}]
        assert estimate_tools_tokens(tools) > 0

    def test_max_input_tokens_reserves_margin(self):
        budget = max_input_tokens(262144, 16384, None)
        assert budget < 262144 - 16384


class TestContextLengthError:
    def test_detects_openai_context_error(self):
        err = openai.BadRequestError(
            "context",
            response=type("R", (), {"status_code": 400})(),
            body={"error": {"message": "maximum context length"}},
        )
        assert _is_context_length_error(err)

    def test_ignores_other_bad_request(self):
        err = openai.BadRequestError(
            "other",
            response=type("R", (), {"status_code": 400})(),
            body={"error": {"message": "invalid model"}},
        )
        assert not _is_context_length_error(err)
