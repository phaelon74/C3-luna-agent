"""Run NZBGet diagnostics MCP over stdio."""

from __future__ import annotations

import atexit
import os
import sys

from nzbget_diagnostics.client import NzbgetClient
from nzbget_diagnostics.mcp_server import build_nzbget_app


def _truthy(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def main() -> None:
    if "--selftest" in sys.argv:
        host = os.environ.get("NZBGET_HOST", "").strip()
        pw = os.environ.get("NZBGET_PASSWORD", "").strip()
        if not host or not pw:
            print("selftest: set NZBGET_HOST and NZBGET_PASSWORD", file=sys.stderr)
            sys.exit(1)
        port = int(os.environ.get("NZBGET_PORT", "6789") or "6789")
        user = os.environ.get("NZBGET_USERNAME", "nzbget").strip() or "nzbget"
        https = _truthy(os.environ.get("NZBGET_USE_HTTPS", "false") or "false")
        client = NzbgetClient(host, port, user, pw, use_https=https)
        try:
            v = client.call("version")
            print("OK", v)
        finally:
            client.close()
        sys.exit(0)

    host = os.environ.get("NZBGET_HOST", "").strip()
    pw = os.environ.get("NZBGET_PASSWORD", "").strip()
    if not host or not pw:
        print("mcp-entrypoint: set NZBGET_HOST and NZBGET_PASSWORD in the container environment.", file=sys.stderr)
        sys.exit(1)

    port = int(os.environ.get("NZBGET_PORT", "6789") or "6789")
    user = os.environ.get("NZBGET_USERNAME", "nzbget").strip() or "nzbget"
    https = _truthy(os.environ.get("NZBGET_USE_HTTPS", "false") or "false")

    client = NzbgetClient(host, port, user, pw, use_https=https)
    atexit.register(client.close)
    app = build_nzbget_app(client)
    app.run(transport="stdio")


if __name__ == "__main__":
    main()
