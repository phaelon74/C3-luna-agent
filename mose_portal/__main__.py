"""Entry point: ``python -m mose_portal`` (stdio MCP server)."""

from __future__ import annotations

import logging
import os

from mose_portal.mcp_server import build_app


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("MCP_PORTAL_LOG_LEVEL", "WARNING"),
        format="%(levelname)s %(name)s %(message)s",
    )
    app = build_app()
    app.run(transport="stdio")


if __name__ == "__main__":
    main()
