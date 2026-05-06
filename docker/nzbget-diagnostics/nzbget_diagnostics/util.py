"""Shared helpers (copied from arr_diagnostics pattern; image-local)."""

from __future__ import annotations

import functools
import json
import traceback
from typing import Any

import httpx


def json_response(data: Any, max_chars: int = 20000) -> str:
    s = json.dumps(data, indent=2, default=str)
    if len(s) > max_chars:
        return s[:max_chars] + f"\n\n... truncated ({len(s)} chars total)"
    return s


def safe_tool(fn: Any) -> Any:
    """Convert exceptions into JSON so stdio MCP session stays alive."""

    @functools.wraps(fn)
    def _wrapped(*args: Any, **kwargs: Any) -> str:
        try:
            return fn(*args, **kwargs)
        except httpx.HTTPStatusError as e:
            body = ""
            try:
                body = (e.response.text or "")[:4000]
            except Exception:
                pass
            return json.dumps({
                "error": "http_error",
                "http_status": e.response.status_code,
                "tool": fn.__name__,
                "body": body,
            })
        except httpx.HTTPError as e:
            return json.dumps({
                "error": "transport_error",
                "tool": fn.__name__,
                "detail": repr(e),
            })
        except Exception as e:  # noqa: BLE001
            return json.dumps({
                "error": "tool_unhandled_exception",
                "tool": fn.__name__,
                "type": type(e).__name__,
                "detail": str(e)[:1000],
                "trace": traceback.format_exc(limit=4)[-2000:],
            })

    return _wrapped


def safe_tool_decorator(mcp_tool_factory: Any) -> Any:
    def _decorator(*d_args: Any, **d_kwargs: Any) -> Any:
        inner = mcp_tool_factory(*d_args, **d_kwargs)

        def _apply(fn: Any) -> Any:
            return inner(safe_tool(fn))

        return _apply

    return _decorator


def redact_config(config: Any) -> Any:
    """Redact password-like keys from NZBGet ``config`` list of ``{Name, Value}``."""
    if not isinstance(config, list):
        return config
    out: list[dict[str, Any]] = []
    for row in config:
        if not isinstance(row, dict):
            out.append(row)  # type: ignore[arg-type]
            continue
        name = str(row.get("Name", ""))
        val = row.get("Value")
        lower = name.lower()
        if "password" in lower or lower.endswith("apikey") or name.endswith("ApiKey"):
            out.append({"Name": name, "Value": "***"})
        else:
            out.append({"Name": name, "Value": val})
    return out
