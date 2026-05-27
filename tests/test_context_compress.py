"""Tests for chunk-and-summarize context compression."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from mose.config import Config, ContextCompressConfig
from mose.context_compress import (
    chunk_text_by_lines,
    compress_text_if_needed,
    estimate_text_tokens,
    init_context_compress,
    max_input_tokens,
)


@pytest.fixture
def compress_config():
    cfg = Config()
    cfg.context_compress = ContextCompressConfig(enabled=True, chunk_input_tokens=100)
    init_context_compress(cfg)
    return cfg


class TestChunking:
    def test_single_chunk_when_small(self):
        assert chunk_text_by_lines("hello", 1000) == ["hello"]

    def test_splits_on_lines(self):
        raw = "\n".join(f"line {i}" for i in range(200))
        chunks = chunk_text_by_lines(raw, 50)
        assert len(chunks) > 1
        assert "".join(chunks) == raw


class TestCompressText:
    @pytest.mark.asyncio
    async def test_passthrough_under_budget(self, compress_config):
        llm = MagicMock()
        raw = "small payload"
        out = await compress_text_if_needed(
            raw,
            llm=llm,
            query_context="test",
            max_output_tokens=estimate_text_tokens(raw) + 100,
            source="test",
        )
        assert out == raw
        llm.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_summarizes_when_over_budget(self, compress_config, tmp_path):
        llm = MagicMock()
        llm.chat = AsyncMock(
            return_value=MagicMock(content="summary line with id=42"),
        )
        raw = "x" * 5000
        budget = 50
        out = await compress_text_if_needed(
            raw,
            llm=llm,
            query_context="queue status",
            max_output_tokens=budget,
            source="mcp_test",
            root=tmp_path,
        )
        assert llm.chat.await_count >= 1
        assert "summary" in out.lower() or "42" in out
        assert "saved to" in out
        assert (tmp_path / "data" / "tool_outputs").exists()


class TestBudget:
    def test_max_input_tokens_reserves_margin(self, compress_config):
        budget = max_input_tokens(262144, 16384, None)
        assert budget < 262144 - 16384
