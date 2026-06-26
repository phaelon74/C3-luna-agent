#!/usr/bin/env python3
"""Run one scheduled task immediately (full agent loop + MCP).

Usage:
  docker compose exec mose-agent python /tmp/run_scheduled_task_now.py sonarr-queue-daily-purge
"""

from __future__ import annotations

import asyncio
import json
import sys

from mose.agent import Agent
from mose.config import load_config
from mose.llm import create_llm_client
from mose.memory import MemoryManager
from mose.mcp_manager import MCPManager
from mose.tools import init_skills_dir, init_tool_registry, init_workspace


async def main(slug: str) -> int:
    config = load_config()
    init_workspace(config.agent.workspace, config.agent.allow_read_outside)
    init_skills_dir(config.agent.skills_path)
    memory = MemoryManager(config.memory)
    task = memory.get_scheduled_task(slug)
    if task is None:
        print(f"Error: unknown scheduled task '{slug}'", file=sys.stderr)
        memory.close()
        return 2
    mcp = MCPManager()
    await mcp.load_servers(config.root_dir / "mcp_servers.json")
    init_tool_registry(mcp, config)
    llm = create_llm_client(config.llm)
    agent = Agent(config, llm, memory, mcp)
    try:
        result = await agent.run_scheduled_task(task)
        print(json.dumps(result, indent=2))
        task2 = memory.get_scheduled_task(slug)
        if task2 is not None:
            print(
                json.dumps(
                    {
                        "slug": slug,
                        "last_status": task2.last_status,
                        "consecutive_failures": task2.consecutive_failures,
                    },
                    indent=2,
                )
            )
        return 0 if result.get("status") == "ok" else 1
    finally:
        await mcp.close()
        memory.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <scheduled-task-slug>", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(asyncio.run(main(sys.argv[1].strip())))
