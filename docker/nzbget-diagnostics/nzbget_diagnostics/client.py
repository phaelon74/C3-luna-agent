"""Synchronous httpx client for NZBGet JSON-RPC (``/jsonrpc``)."""

from __future__ import annotations

import json
from typing import Any

import httpx


class NzbgetClient:
    """POST JSON-RPC 2.0 to NZBGet; HTTP basic auth."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        *,
        use_https: bool = False,
        timeout: float = 120.0,
    ) -> None:
        scheme = "https" if use_https else "http"
        self._url = f"{scheme}://{host.strip()}:{int(port)}/jsonrpc"
        self._client = httpx.Client(
            timeout=timeout,
            auth=(username, password),
            headers={"Content-Type": "application/json"},
        )

    def close(self) -> None:
        self._client.close()

    def call(self, method: str, *params: Any) -> Any:
        """Call NZBGet RPC method with positional ``params`` only (required by NZBGet)."""
        body = {
            "jsonrpc": "2.0",
            "method": method,
            "params": list(params),
            "id": 1,
        }
        r = self._client.post(self._url, content=json.dumps(body))
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"nzbget {method}: non-object JSON response")
        if data.get("error"):
            raise RuntimeError(f"nzbget {method} error: {data['error']!r}")
        return data.get("result")
