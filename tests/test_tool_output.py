"""Tests for the smart output pipeline."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from mose.config import Config, ContextCompressConfig
from mose.context_compress import init_context_compress
from mose.tool_output import (
    _persist_output,
    process_large_output,
)


@pytest.fixture(autouse=True)
def _compress_cfg():
    cfg = Config()
    cfg.context_compress = ContextCompressConfig(
        enabled=True,
        chunk_input_tokens=100,
        large_output_threshold=100,
    )
    init_context_compress(cfg)


class TestPersistOutput:
    def test_creates_file(self, tmp_path):
        path = _persist_output("hello world", "test_source", tmp_path)
        assert path.exists()
        assert path.read_text() == "hello world"

    def test_deterministic_filename(self, tmp_path):
        p1 = _persist_output("same content", "src", tmp_path)
        p2 = _persist_output("same content", "src", tmp_path)
        assert p1 == p2

    def test_different_content_different_file(self, tmp_path):
        p1 = _persist_output("content A", "src", tmp_path)
        p2 = _persist_output("content B", "src", tmp_path)
        assert p1 != p2

    def test_creates_output_dir(self, tmp_path):
        _persist_output("test", "src", tmp_path)
        assert (tmp_path / "data" / "tool_outputs").is_dir()


class TestProcessLargeOutput:
    @pytest.mark.asyncio
    async def test_small_output_passthrough(self, tmp_path):
        result = await process_large_output(
            "small output", "context", "test", None, root=tmp_path
        )
        assert result == "small output"
        output_dir = tmp_path / "data" / "tool_outputs"
        assert not output_dir.exists()

    @pytest.mark.asyncio
    async def test_large_output_compressed(self, tmp_path):
        llm = MagicMock()
        llm.chat = AsyncMock(return_value=MagicMock(content="relevant excerpt"))
        raw = "x" * 5000
        result = await process_large_output(
            raw, "context", "test", llm, root=tmp_path
        )
        assert "saved to" in result
        output_dir = tmp_path / "data" / "tool_outputs"
        assert output_dir.exists()
        files = list(output_dir.iterdir())
        assert len(files) == 1
        assert files[0].read_text() == raw

    @pytest.mark.asyncio
    async def test_large_output_without_llm(self, tmp_path):
        raw = "x" * 5000
        result = await process_large_output(
            raw, "context", "test", None, root=tmp_path
        )
        assert "saved to" in result
        assert (tmp_path / "data" / "tool_outputs").exists()
