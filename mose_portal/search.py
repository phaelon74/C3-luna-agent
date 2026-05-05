"""Search over aggregated MCP tools: keyword ranking + optional nomic embeddings."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from typing import Any

from mose_portal import codegen

log = logging.getLogger("mose_portal.search")

_EMBEDDER: Any = None


@dataclass
class SearchHit:
    """One tool match for ``portal_codemode_search`` (``example`` is required for MoE models)."""

    name: str
    ts_signature: str
    description: str
    example: str

    def to_json_dict(self) -> dict[str, str]:
        return asdict(self)


def _tool_document(tool: dict[str, Any]) -> str:
    name = str(tool.get("name", ""))
    desc = str(tool.get("description") or "")
    schema = tool.get("input_schema") or {}
    if not isinstance(schema, dict):
        schema = {}
    return f"{name}\n{desc}\n{json.dumps(schema, default=str)[:4000]}"


def re_split_tokens(text: str) -> list[str]:
    out: list[str] = []
    cur: list[str] = []
    for ch in text:
        if ch.isalnum() or ch in {"_", "-"}:
            cur.append(ch)
        else:
            if cur:
                out.append("".join(cur))
                cur = []
    if cur:
        out.append("".join(cur))
    return [x.lower() for x in out if x]


def _keyword_score(query: str, text: str, doc_tokens: list[str]) -> float:
    q = query.lower().strip()
    if not q:
        return 0.0
    text_l = text.lower()
    score = 0.0
    if q in text_l:
        score += 8.0
    doc_token_set = set(doc_tokens)
    for token in q.replace("/", " ").split():
        token = token.strip()
        if len(token) < 2:
            continue
        if token in text_l:
            score += 3.0
        if token in doc_token_set:
            score += 1.0
        else:
            for word in doc_token_set:
                if len(word) >= 3 and (word.startswith(token) or token.startswith(word)):
                    score += 0.5
                    break
    return score


def _tool_to_hit(tool: dict[str, Any]) -> SearchHit:
    _iface_block, sig, ex = codegen.tool_row_to_typescript(tool)
    return SearchHit(
        name=str(tool["name"]),
        ts_signature=sig,
        description=str(tool.get("description") or ""),
        example=ex,
    )


def _get_embedder() -> Any:
    global _EMBEDDER
    if _EMBEDDER is None:
        from sentence_transformers import SentenceTransformer

        _EMBEDDER = SentenceTransformer(
            "nomic-ai/nomic-embed-text-v1.5",
            truncate_dim=384,
        )
        log.info("embedder_loaded model=nomic-embed-text-v1.5 dims=384")
    return _EMBEDDER


def _cosine_dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))


async def _embed_rank(
    tools: list[dict[str, Any]],
    query: str,
    top_k: int,
) -> list[SearchHit]:
    model = _get_embedder()

    def _qvec() -> list[float]:
        v = model.encode(f"search_query: {query}", normalize_embeddings=True)
        return v.tolist()

    qvec = await asyncio.to_thread(_qvec)

    scores: list[tuple[float, dict[str, Any]]] = []

    def _score_one(doc: str) -> float:
        v = model.encode(f"search_document: {doc}", normalize_embeddings=True)
        return _cosine_dot(qvec, v.tolist())

    for t in tools:
        doc = _tool_document(t)
        sim = await asyncio.to_thread(_score_one, doc)
        kw = _keyword_score(query, doc, re_split_tokens(doc))
        # Blend so short keyword queries still help
        combined = 0.75 * float(sim) + 0.25 * min(kw / 10.0, 1.0)
        scores.append((combined, t))

    scores.sort(key=lambda x: (-x[0], x[1].get("name", "")))
    chosen = [t for _, t in scores[: max(1, top_k)]]
    return [_tool_to_hit(t) for t in chosen]


def _keyword_rank(tools: list[dict[str, Any]], query: str, top_k: int) -> list[SearchHit]:
    if not query.strip():
        chosen = sorted(tools, key=lambda t: str(t.get("name", "")))[: max(1, top_k)]
        return [_tool_to_hit(t) for t in chosen]

    scored: list[tuple[float, dict[str, Any]]] = []
    for t in tools:
        doc = _tool_document(t)
        scored.append((_keyword_score(query, doc, re_split_tokens(doc)), t))
    scored.sort(key=lambda x: (-x[0], str(x[1].get("name", ""))))
    # Drop zero-scored entries when query is non-empty (no point listing irrelevant tools).
    relevant = [(s, t) for s, t in scored if s > 0.0]
    chosen = [t for _, t in relevant[: max(1, top_k)]]
    return [_tool_to_hit(t) for t in chosen]


async def search_tools(
    tools: list[dict[str, Any]],
    *,
    query: str,
    top_k: int = 10,
    use_embeddings: bool = False,
) -> list[SearchHit]:
    """Rank tools and return rich hits (signature + required ``example``)."""
    if not tools:
        return []
    top_k = max(1, min(int(top_k), 50))

    if use_embeddings:
        try:
            return await _embed_rank(tools, query, top_k)
        except Exception:
            log.exception("embedding_search_failed; falling back to keyword")
    return _keyword_rank(tools, query, top_k)
