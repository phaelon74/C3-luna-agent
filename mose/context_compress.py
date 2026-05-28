"""Chunk-and-summarize compression when payloads would exceed the model context bound."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Protocol

from mose.config import Config, ContextCompressConfig
from mose.observe import get_logger, log_duration, log_event

OUTPUT_DIR = "data/tool_outputs"

logger = get_logger("context_compress")

DEFAULT_CHARS_PER_TOKEN = 3.0
TOOL_CHARS_PER_TOKEN = 2.5
TOOL_SCHEMA_CHARS_PER_TOKEN = 3.0

_SUMMARIZE_SYSTEM = """\
You compress tool or API output for another AI assistant.
Preserve exact facts: IDs, names, counts, statuses, error messages, timestamps, URLs.
Focus on information relevant to the user's question.
Do not invent data. If something is missing from this chunk, do not guess.
Use concise structured text (bullets or short sections are fine)."""

_config: ContextCompressConfig | None = None
_llm_context_window: int = 98304
_llm_max_tokens: int = 16384
_vision_tokens_per_image: int = 1536


class LLMExtractor(Protocol):
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
    ) -> Any: ...


def _ensure_output_dir(root: Path) -> Path:
    out = root / OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    return out


def persist_output(raw: str, source: str, root: Path) -> Path:
    """Save full output to disk. Returns the file path."""
    out_dir = _ensure_output_dir(root)
    h = hashlib.sha256(raw.encode()).hexdigest()[:12]
    safe_source = re.sub(r"[^\w\-.]", "_", source)[:60]
    path = out_dir / f"{safe_source}_{h}.txt"
    path.write_text(raw, encoding="utf-8")
    log_event(logger, "output_persisted", path=str(path), size=len(raw))
    return path


def init_context_compress(config: Config) -> None:
    """Register compression settings from loaded config (call once at startup)."""
    global _config, _llm_context_window, _llm_max_tokens, _vision_tokens_per_image
    _config = config.context_compress
    _llm_context_window = config.llm.context_window
    _llm_max_tokens = config.llm.max_tokens
    _vision_tokens_per_image = config.llm.vision_tokens_per_image


def _cfg() -> ContextCompressConfig:
    if _config is None:
        from mose.config import ContextCompressConfig

        return ContextCompressConfig()
    return _config


def _chars_per_token_for_role(role: str | None) -> float:
    if role == "tool":
        return TOOL_CHARS_PER_TOKEN
    return DEFAULT_CHARS_PER_TOKEN


def estimate_text_tokens(text: str, *, role: str = "tool") -> int:
    divisor = _chars_per_token_for_role(role)
    return int(len(text) / divisor)


def _estimate_content_tokens(content: Any, role: str | None) -> int:
    if isinstance(content, list):
        tokens = 0
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                tokens += int(len(str(part.get("text") or "")) / DEFAULT_CHARS_PER_TOKEN)
            elif part.get("type") == "image_url":
                tokens += _vision_tokens_per_image
        return tokens
    divisor = _chars_per_token_for_role(role)
    return int(len(str(content or "")) / divisor)


def estimate_tokens(messages: list[dict]) -> int:
    """Rough token estimate from message list."""
    tokens = 0
    for m in messages:
        tokens += _estimate_content_tokens(m.get("content"), m.get("role"))
        for tc in m.get("tool_calls", []):
            fn = tc.get("function")
            args = fn.get("arguments", "") if isinstance(fn, dict) else str(tc)
            tokens += int(len(str(args)) / DEFAULT_CHARS_PER_TOKEN)
    return tokens


def estimate_tools_tokens(tools: list[dict[str, Any]] | None) -> int:
    if not tools:
        return 0
    try:
        blob = json.dumps(tools, ensure_ascii=False)
    except (TypeError, ValueError):
        blob = str(tools)
    return int(len(blob) / TOOL_SCHEMA_CHARS_PER_TOKEN)


def max_input_tokens(
    context_window: int | None = None,
    max_completion: int | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> int:
    """Input token budget for chat messages (excludes completion tokens)."""
    cfg = _cfg()
    cw = context_window if context_window is not None else _llm_context_window
    mc = max_completion if max_completion is not None else _llm_max_tokens
    margin = cfg.safety_margin_tokens
    return max(
        4096,
        cw - mc - margin - estimate_tools_tokens(tools),
    )


def tokens_to_chars(token_budget: int, *, role: str = "tool") -> int:
    return int(token_budget * _chars_per_token_for_role(role))


def default_tool_result_token_budget(
    tools: list[dict[str, Any]] | None = None,
) -> int:
    """Per-tool result target when compressing ingress payloads."""
    total = max_input_tokens(tools=tools)
    return max(2048, total // 8)


def chunk_text_by_lines(text: str, max_chars: int) -> list[str]:
    """Split text on line boundaries without discarding content."""
    if max_chars <= 0:
        return [text] if text else [""]
    if len(text) <= max_chars:
        return [text]

    lines = text.splitlines(keepends=True)
    if not lines:
        return [text[i : i + max_chars] for i in range(0, len(text), max_chars)] or [""]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        if len(line) > max_chars:
            if current:
                chunks.append("".join(current))
                current = []
                current_len = 0
            for i in range(0, len(line), max_chars):
                chunks.append(line[i : i + max_chars])
            continue
        if current_len + len(line) > max_chars and current:
            chunks.append("".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += len(line)
    if current:
        chunks.append("".join(current))
    return chunks or [text]


async def _summarize_chunk(
    llm: LLMExtractor,
    chunk: str,
    query_context: str,
    index: int,
    total: int,
) -> str:
    messages = [
        {"role": "system", "content": _SUMMARIZE_SYSTEM},
        {
            "role": "user",
            "content": (
                f"User question: {query_context or '(general)'}\n\n"
                f"--- Chunk {index}/{total} ---\n\n{chunk}"
            ),
        },
    ]
    try:
        response = await llm.chat(messages, tools=None, temperature=0.2)
        return (response.content or "").strip() or "(empty chunk summary)"
    except Exception:
        logger.exception("chunk summarization failed")
        return f"(summarization failed for chunk {index}/{total})"


async def _map_reduce_summarize(
    llm: LLMExtractor,
    raw: str,
    query_context: str,
    *,
    max_output_tokens: int,
    source: str,
    root: Path | None,
    recursion_depth: int = 0,
) -> str:
    cfg = _cfg()
    if not cfg.enabled or not llm:
        return raw

    chunk_chars = tokens_to_chars(cfg.chunk_input_tokens, role="tool")
    chunks = chunk_text_by_lines(raw, chunk_chars)
    summaries: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        summaries.append(await _summarize_chunk(llm, chunk, query_context, i, len(chunks)))

    assembled = "\n\n".join(
        f"--- Chunk {i}/{len(summaries)} ---\n{s}" for i, s in enumerate(summaries, start=1)
    )
    header = f"[Compressed summary of {source}; {len(raw)} chars original, {len(chunks)} chunks]\n\n"
    result = header + assembled

    if estimate_text_tokens(result) > max_output_tokens:
        if recursion_depth >= cfg.max_recursion_depth:
            log_event(
                logger,
                "context_compress_max_depth",
                source=source,
                depth=recursion_depth,
            )
            return result
        log_event(
            logger,
            "context_compress_recursion",
            source=source,
            depth=recursion_depth + 1,
            before_tokens=estimate_text_tokens(result),
        )
        return await _map_reduce_summarize(
            llm,
            result,
            query_context,
            max_output_tokens=max_output_tokens,
            source=f"{source}_L{recursion_depth + 1}",
            root=root,
            recursion_depth=recursion_depth + 1,
        )
    return result


async def compress_text_if_needed(
    raw: str,
    *,
    llm: LLMExtractor | None,
    query_context: str,
    max_output_tokens: int,
    source: str,
    root: Path | None = None,
) -> str:
    """Return raw unchanged if within budget; else persist, chunk, summarize, reassemble."""
    cfg = _cfg()
    if not cfg.enabled:
        return raw

    before = estimate_text_tokens(raw)
    if before <= max_output_tokens:
        log_event(
            logger,
            "context_compress_skipped",
            source=source,
            before_tokens=before,
            budget=max_output_tokens,
        )
        return raw

    if llm is None:
        log_event(logger, "context_compress_no_llm", source=source, before_tokens=before)
        return raw

    if root is None:
        root = Path(__file__).resolve().parent.parent

    path = persist_output(raw, source, root)
    log_event(
        logger,
        "context_compress_started",
        source=source,
        before_tokens=before,
        budget=max_output_tokens,
        chars=len(raw),
    )

    with log_duration(logger, "context_compress", source=source, chars=len(raw)):
        compressed = await _map_reduce_summarize(
            llm,
            raw,
            query_context,
            max_output_tokens=max_output_tokens,
            source=source,
            root=root,
        )

    after = estimate_text_tokens(compressed)
    footer = (
        f"\n\n---\nFull output ({len(raw)} chars) saved to: {path}\n"
        "Use read_file or bash with grep to search further."
    )
    result = compressed + footer
    log_event(
        logger,
        "context_compress_complete",
        source=source,
        before_tokens=before,
        after_tokens=estimate_text_tokens(result),
    )
    return result


def _get_message_blocks(messages: list[dict]) -> list[tuple[int, int]]:
    blocks: list[tuple[int, int]] = []
    i = 0
    while i < len(messages):
        m = messages[i]
        role = m.get("role")
        if role in ("system", "user"):
            blocks.append((i, i + 1))
            i += 1
        elif role == "assistant":
            tool_calls = m.get("tool_calls", [])
            if not tool_calls:
                blocks.append((i, i + 1))
                i += 1
            else:
                blocks.append((i, i + 1 + len(tool_calls)))
                i += 1 + len(tool_calls)
        elif role == "tool":
            blocks.append((i, i + 1))
            i += 1
        else:
            blocks.append((i, i + 1))
            i += 1
    return blocks


async def _compress_message_content(
    msg: dict,
    *,
    llm: LLMExtractor,
    query_context: str,
    target_tokens: int,
    source: str,
    root: Path | None,
) -> dict:
    content = msg.get("content")
    if isinstance(content, list):
        return msg
    role = msg.get("role") or "user"
    content = str(content or "")
    if not content:
        return msg
    compressed = await compress_text_if_needed(
        content,
        llm=llm,
        query_context=query_context,
        max_output_tokens=target_tokens,
        source=f"{source}_{role}",
        root=root,
    )
    if compressed == content:
        return msg
    out = dict(msg)
    out["content"] = compressed
    return out


async def compress_messages_if_needed(
    messages: list[dict],
    *,
    llm: LLMExtractor | None,
    max_input_tokens: int,
    query_context: str,
    preserve_system: bool = True,
    root: Path | None = None,
) -> list[dict]:
    """Compress tool (and then other) message bodies until the list fits the input budget."""
    cfg = _cfg()
    if not cfg.enabled or not llm:
        return messages

    before = estimate_tokens(messages)
    if before <= max_input_tokens:
        log_event(
            logger,
            "context_compress_skipped",
            source="messages",
            before_tokens=before,
            budget=max_input_tokens,
        )
        return messages

    if root is None:
        root = Path(__file__).resolve().parent.parent

    system_tokens = 0
    if preserve_system and messages and messages[0].get("role") == "system":
        system_tokens = estimate_tokens([messages[0]])
        if system_tokens > max_input_tokens:
            log_event(
                logger,
                "context_compress_system_too_large",
                system_tokens=system_tokens,
                budget=max_input_tokens,
            )
            return messages

    log_event(
        logger,
        "context_compress_messages_started",
        before_tokens=before,
        budget=max_input_tokens,
        message_count=len(messages),
    )

    out = [dict(m) for m in messages]
    content_budget = max(512, max_input_tokens - system_tokens)

    # Pass 1: compress largest tool messages first.
    tool_indices = [
        i for i, m in enumerate(out)
        if m.get("role") == "tool" and len(str(m.get("content") or "")) > 0
    ]
    tool_indices.sort(
        key=lambda i: estimate_text_tokens(str(out[i].get("content") or "")),
        reverse=True,
    )
    per_tool_target = max(512, content_budget // max(1, len(tool_indices)))

    for i in tool_indices:
        if estimate_tokens(out) <= max_input_tokens:
            break
        out[i] = await _compress_message_content(
            out[i],
            llm=llm,
            query_context=query_context,
            target_tokens=per_tool_target,
            source=f"tool_msg_{i}",
            root=root,
        )

    # Pass 2: compress other non-system messages (oldest first), skip assistant+tool_call pairs' structure.
    if estimate_tokens(out) > max_input_tokens:
        other_indices = [
            i for i, m in enumerate(out)
            if m.get("role") in ("user", "assistant")
            and not (preserve_system and i == 0)
            and len(str(m.get("content") or "")) > 0
            and not m.get("tool_calls")
        ]
        per_other = max(512, content_budget // max(1, len(other_indices)))
        for i in other_indices:
            if estimate_tokens(out) <= max_input_tokens:
                break
            out[i] = await _compress_message_content(
                out[i],
                llm=llm,
                query_context=query_context,
                target_tokens=per_other,
                source=f"msg_{i}",
                root=root,
            )

    # Pass 3: if still over, run another pass on tool messages with tighter budget.
    if estimate_tokens(out) > max_input_tokens:
        tight = max(256, content_budget // 4)
        for i in tool_indices:
            if estimate_tokens(out) <= max_input_tokens:
                break
            out[i] = await _compress_message_content(
                out[i],
                llm=llm,
                query_context=query_context,
                target_tokens=tight,
                source=f"tool_msg_tight_{i}",
                root=root,
            )

    after = estimate_tokens(out)
    log_event(
        logger,
        "context_compress_messages_complete",
        before_tokens=before,
        after_tokens=after,
        budget=max_input_tokens,
    )
    return out
