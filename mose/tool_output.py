"""Smart output pipeline: persist large outputs and compress via chunk-and-summarize when over budget."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from mose.context_compress import (
    OUTPUT_DIR,
    compress_text_if_needed,
    default_tool_result_token_budget,
    estimate_text_tokens,
    persist_output,
)
from mose.observe import get_logger

logger = get_logger("tool_output")

# Legacy name: callers compare against this; 0 means derive from context at runtime.
LARGE_OUTPUT_THRESHOLD = 10_000


class LLMExtractor(Protocol):
    """Protocol for LLM extraction — matches LLMClient.chat signature."""

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
    ) -> Any: ...


def _persist_output(raw: str, source: str, root: Path) -> Path:
    """Backward-compatible alias for tests."""
    return persist_output(raw, source, root)


def _effective_large_threshold() -> int:
    from mose.context_compress import _cfg, tokens_to_chars

    cfg = _cfg()
    if cfg.large_output_threshold > 0:
        return cfg.large_output_threshold
    budget = default_tool_result_token_budget()
    return tokens_to_chars(budget, role="tool")


async def process_large_output(
    raw: str,
    context: str,
    source: str,
    llm: LLMExtractor | None,
    root: Path | None = None,
) -> str:
    """Return raw when small; otherwise compress only if over the tool-result token budget."""
    threshold = _effective_large_threshold()
    if len(raw) <= threshold and estimate_text_tokens(raw) <= default_tool_result_token_budget():
        return raw

    if root is None:
        root = Path(__file__).resolve().parent.parent

    if llm is None:
        path = persist_output(raw, source, root)
        return (
            f"(Output too large for inline display: {len(raw)} chars. "
            f"Full output saved to: {path})"
        )

    return await compress_text_if_needed(
        raw,
        llm=llm,
        query_context=context,
        max_output_tokens=default_tool_result_token_budget(),
        source=source,
        root=root,
    )
