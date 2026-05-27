"""Core agent loop: receive message, retrieve memory, call LLM, execute tools, respond."""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from typing import Any, Callable

import openai

from mose.config import Config, LearningConfig
from mose.context_compress import (
    compress_messages_if_needed,
    compress_text_if_needed,
    default_tool_result_token_budget,
    init_context_compress,
    max_input_tokens as compute_max_input_tokens,
)
from mose.learning import SkillLearner
from mose.llm import LLMClient
from mose.memory import MemoryManager
from mose.mcp_manager import MCPManager
from mose.observe import get_logger, log_event, log_duration
from mose.tracker_decision import format_tracker_recovery_message, init_tracker_decision_runtime
from mose.trackers import TrackerScheduler
from mose.tools import (
    NATIVE_TOOLS,
    call_native_tool,
    execute_mcp_tool,
    init_tracker_tool_context,
    is_native_tool,
    verify_tool_result,
)

logger = get_logger("agent")


def _is_context_length_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "maximum context length" in msg or "input_tokens" in msg


def _coerce_tool_arguments(raw: Any) -> dict[str, Any]:
    """Normalize LLM tool-call arguments to a dict (OpenAI wire format uses JSON string)."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return {}
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return {}
    return {}


SYSTEM_PROMPT_TEMPLATE = """\
You are Mose, an AI assistant running in Cloud3's Infrastructure. You are knowledgeable, precise, and concise. \
You think step by step on complex problems but keep routine answers brief.

## Capabilities
You have persistent memory (facts survive across sessions), access to a bash shell, the local filesystem, \
and the web. You can read/write files, run commands, search the internet, and fetch web pages. \
You are expected to use these tools proactively — do not describe what you could do, just do it.

## Backend Systems (MANDATORY routing)
Plex, Sonarr, Radarr, NZBGet, paper_db, and every other integrated backend are reached **only** through Code Mode:
1. ``mcp-portal__portal_codemode_search`` — discover the right tool.
2. ``mcp-portal__portal_codemode_execute`` — run TypeScript that calls ``mcp.<server>.<tool>(...)`` and ``console.log``s the answer.

You **MUST NOT** use ``bash``, shell HTTP clients, ``docker exec`` into MCP sidecars, or ``sre_execute`` to reach these services — credentials exist only inside MCP sidecars. If the question is about Plex / Sonarr / Radarr / NZBGet / paper_db **on your systems**, your **first** tool call must be ``mcp-portal__portal_codemode_search``. For **public release notes** (e.g. latest Plex Media Server version on plex.tv), use ``web_search`` and ``web_fetch`` only — not Code Mode and not the local Plex API.

## Tool Usage
- **bash**: Read-only system commands (status, logs, queries) on **this host**. Never use it to reach Plex / Sonarr / Radarr / paper_db (use Code Mode — see "Backend Systems" above).
- **sre_execute**: State-changing commands (restart, update, config changes) on **this host**. Requires human approval. Never use for backend systems — Code Mode handles their approval flow.
- **read_file / write_file**: File I/O. Relative paths resolve to the workspace. Writes outside workspace are blocked.
- **list_directory**: Browse the filesystem before reading specific files.
- **load_skill**: Load full text of one domain skill by name when using condensed skill index (level_0).
- **web_search**: Search the web via DuckDuckGo when you need current information, documentation, or facts you're unsure about.
- **web_fetch**: Fetch and read a specific URL. Use after web_search to get details from a result.
- **delegate**: Hand off a self-contained subtask to a sub-agent with its own tool loop. \
Use for multi-step research, complex file operations, or anything that benefits from focused context.
- **code_task**: Delegate a coding task to a sub-agent that writes code, runs it, checks results, \
and iterates on failures. Use for scripts, scrapers, automation, or any task requiring write-run-fix cycles. \
Prefer this over delegate for coding work.
- **mcp-portal__portal_codemode_search / portal_codemode_execute**: How you reach every backend system. The sandbox exposes a global ``mcp`` object whose shape mirrors upstream MCP servers in snake_case (e.g. ``mcp.plex_ops_admin.sessions_get_active``, ``mcp.sonarr_diagnostics.sonarr_get_queue``, ``mcp.nzbget_diagnostics.nzbget_listgroups``, ``mcp.paper_db.index_paper``). Each call returns a parsed object (the sandbox JSON-decodes the upstream response automatically). **You MUST report only what ``console.log`` actually printed** — never invent counts, IDs, or "queue is empty" claims when the output is empty; that means your code accessed the wrong field, not that the data is missing. If ``execute`` returns a non-empty ``errors[]``, read it (kind: ts_compile / runtime / mcp_call; line; failing mcp call), fix, and retry. Do not guess.

### Worked example — answer "what's currently playing on Plex?"
1. Call ``mcp-portal__portal_codemode_search`` with ``query="active plex sessions"`` → finds ``sessions_get_active``.
2. Call ``mcp-portal__portal_codemode_execute`` with code:
   ``const sessions = await mcp.plex_ops_admin.sessions_get_active({{}});``
   ``// Always log the raw shape first when you don't know it. Don't assume.``
   ``console.log(JSON.stringify(sessions, null, 2));``
3. Read the printed JSON. **If stdout is empty, your access was wrong — log ``sessions`` itself and try again.** Then write a second ``execute`` that walks the real shape and prints exactly what the user asked for.

### Trackers (scheduled collectors)
- Tools: ``tracker_list`` (use ``include_collector: true`` to debug), ``tracker_query``, ``tracker_update``, ``tracker_run_now``, ``tracker_propose``.
- Collectors are TypeScript bodies that must ``console.log(JSON.stringify({{ metrics, snapshot }}))``.
- Before writing or fixing Plex collectors, ``load_skill`` **codemode-collector-conventions** and **plex** (tracker section). Probe API shape first; do not assume MediaContainer for ``sessions_get_active``.
- ``server_get_current_resources``: latest ``timestamp`` row from ``data[]`` only (values already 0–100%). ``sessions_get_active``: flat ``sessions`` + ``total_bitrate_kbps``; parse ``media_info.bitrate`` strings.

## Guidelines
- Act, don't ask. You have tools — use them. Install packages, run commands, create files, scan networks. \
Do it and report the results. Do not ask "would you like me to..." for safe, reversible operations.
- Never tell the user to run commands manually. You have bash. Run the command yourself, read the output, \
and iterate. The user should only need to intervene for physical actions (plugging in cables, rebooting hardware).
- When something fails, try a different approach. If a package install fails, try another method. \
If a scan finds nothing, try different parameters, a different tool, or debug why. Exhaust your options \
before asking the user for help.
- When there are multiple approaches, pick the best one and do it. Explain what you chose and why \
in your response — don't present a menu of options.
- Verify before destructive actions: check before deleting, overwriting, or modifying system config. \
But reading, installing, scanning, and creating are safe — just do them.
- Break complex tasks into steps. Use tools iteratively rather than guessing.
- When you don't know something, look it up (web_search, web_fetch, read docs) rather than guessing \
or asking the user.
- For file creation, use relative paths — they resolve to the workspace below.

## Approach
When given a task that requires multiple steps (e.g., "set up X", "discover devices", "install and test Y"):
1. Research first if needed (web_search, read docs)
2. Install dependencies in the workspace venv or with pip
3. Write and run code/scripts to accomplish the task
4. If something doesn't work, debug it — read errors, try alternatives, search for solutions
5. Report what you did and what the results were

{skills_section}

## Memory
The "Relevant Memories" section below contains facts retrieved from your long-term memory based on \
the current conversation. These may include user preferences, past decisions, project details, or \
previously learned facts. Not all retrieved memories will be relevant — use judgment.

{memory_section}
{summary_section}
{trackers_section}
Current time: {current_time}
Workspace: {workspace}"""


def _skill_blurb(text: str, limit: int = 240) -> str:
    """First heading or paragraph for level_0 index."""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip()[:limit]
        if s and not s.startswith("---"):
            return s[:limit]
    return ""


def _load_skills(skills_dir: Path, mode: str = "full") -> str:
    """Load skills: full concatenation, or level_0 (overview + one-line index + load_skill for detail)."""
    if not skills_dir.exists() or not skills_dir.is_dir():
        return ""
    if mode == "level_0":
        overview_path = skills_dir / "_overview.md"
        overview = ""
        if overview_path.is_file():
            try:
                overview = overview_path.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                logger.warning("Failed to load _overview: %s", e)
        index_lines: list[str] = []
        files = sorted(
            skills_dir.glob("*.md"),
            key=lambda p: (p.name == "_overview.md", p.name),
        )
        for f in files:
            if f.name == "_overview.md":
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                logger.warning("Failed to read skill file %s: %s", f, e)
                continue
            blurb = _skill_blurb(text)
            index_lines.append(f"- **{f.stem}** — {blurb or '(see load_skill)'}")
        index_block = "\n".join(index_lines) if index_lines else ""
        parts = [overview]
        if index_block:
            parts.append("### Skill index (use `load_skill` with the basename for full content)\n\n" + index_block)
        return "\n\n".join(p for p in parts if p.strip())

    files = sorted(skills_dir.glob("*.md"), key=lambda p: (p.name != "_overview.md", p.name))
    if not files:
        return ""
    parts: list[str] = []
    for f in files:
        try:
            parts.append(f.read_text(encoding="utf-8", errors="replace"))
        except Exception as e:
            logger.warning("Failed to load skill file %s: %s", f, e)
    return "\n\n---\n\n".join(parts)


def _format_active_trackers_block(memory: MemoryManager, trackers_cfg: Any) -> str:
    """Compact list of enabled trackers for the system prompt (size-capped)."""
    if not getattr(trackers_cfg, "enabled", True):
        return ""
    rows = memory.list_trackers(enabled_only=True)
    if not rows:
        return ""
    from datetime import datetime, timezone

    max_lines = max(1, int(getattr(trackers_cfg, "active_trackers_max_lines", 12)))
    max_chars = max(100, int(getattr(trackers_cfg, "active_trackers_prompt_chars", 500)))
    lines: list[str] = ["## Active Trackers (standing duties)"]
    for t in rows[:max_lines]:
        lr = ""
        if t.last_run_at:
            try:
                lr = datetime.fromtimestamp(t.last_run_at, tz=timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
            except (OSError, OverflowError, ValueError):
                lr = "?"
        st = (t.last_status or "unknown")[:40]
        sched = f"{t.schedule_seconds}s"
        line = f"- {t.slug}: {t.description[:80]} (every {sched}, last {lr}, {st})"
        lines.append(line)
    if len(rows) > max_lines:
        lines.append(f"- … and {len(rows) - max_lines} more")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 3] + "..."
    return text


def _build_system_prompt(
    memories: list,
    summary: str | None,
    current_time: str,
    workspace: str = "",
    skills_path: str = "",
    learning: LearningConfig | None = None,
    trackers_section: str = "",
) -> str:
    memory_section = ""
    if memories:
        mem_lines = []
        for m in memories:
            mem_lines.append(f"- [{m.memory_type}] {m.content}")
        memory_section = "## Relevant Memories\n" + "\n".join(mem_lines)

    summary_section = ""
    if summary:
        summary_section = f"## Previous Context\n{summary}"

    skills_section = ""
    if skills_path:
        mode = "full"
        if learning and getattr(learning, "skill_loading_mode", "full") == "level_0":
            mode = "level_0"
        content = _load_skills(Path(skills_path), mode=mode)
        if content:
            skills_section = f"\n\n## Cloud3 SRE Environment\n\n{content}\n\n"

    return SYSTEM_PROMPT_TEMPLATE.format(
        memory_section=memory_section,
        summary_section=summary_section,
        trackers_section=trackers_section,
        skills_section=skills_section,
        current_time=current_time,
        workspace=workspace,
    )


class Agent:
    """The orchestrator that ties LLM, memory, and MCP tools together."""

    def __init__(
        self,
        config: Config,
        llm: LLMClient,
        memory: MemoryManager,
        mcp: MCPManager,
        tool_callback: Callable[[str, str, str], None] | None = None,
    ) -> None:
        self.config = config
        self.llm = llm
        self.memory = memory
        self.mcp = mcp
        self.max_tool_rounds = 25  # safety limit on tool call loops
        self.tool_callback = tool_callback
        self._skill_learner = SkillLearner(
            config.learning,
            Path(config.agent.skills_path),
            log_dir=Path(config.learning.review_log_dir),
            proposal_timeout_seconds=int(config.signal.proposal_timeout_seconds),
            build_grace_window_seconds=int(
                getattr(config.learning, "build_grace_window_seconds", 900)
            ),
        )
        self._review_task: asyncio.Task[Any] | None = None
        # Per-session guard: same MCP tool+args + SDK isError twice → skip further calls (no approval spam).
        self._mcp_repeat_guard: dict[str, dict[str, Any]] = {}
        self._tracker_scheduler: TrackerScheduler | None = None
        self._tracker_compact_task: asyncio.Task[Any] | None = None
        self._tracker_compact_runs: int = 0
        init_tracker_tool_context(
            memory=self.memory,
            config=self.config,
            get_scheduler=self._get_tracker_scheduler,
        )
        init_tracker_decision_runtime(
            memory=self.memory,
            get_scheduler=self._get_tracker_scheduler,
        )
        init_context_compress(self.config)

    def _build_llm_tools(self, session_id: str) -> list[dict[str, Any]]:
        """Native tools plus MCP tools from connected servers (local list; never mutates ``NATIVE_TOOLS``)."""
        del session_id  # reserved for future per-session tool shaping
        cfg = self.config.agent
        llm_tools: list[dict[str, Any]] = list(NATIVE_TOOLS)
        if not self.mcp.servers:
            return llm_tools
        mcp_tools = self.mcp.get_all_tools()
        allowed = [s.strip() for s in cfg.inline_mcp_servers if str(s).strip()]
        if allowed:
            allowed_set = frozenset(allowed)
            filtered: list[dict[str, Any]] = []
            for entry in mcp_tools:
                name = str(entry.get("function", {}).get("name", ""))
                if "__" not in name:
                    continue
                server = name.split("__", 1)[0].strip()
                if server in allowed_set:
                    filtered.append(entry)
            mcp_tools = filtered
        llm_tools.extend(mcp_tools)
        return llm_tools

    async def _llm_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        max_input_tokens: int,
        query_context: str,
    ):
        """Call the LLM; on context overflow, compress and retry once."""
        kwargs_tools = tools if tools else None
        try:
            return await self.llm.chat(messages, tools=kwargs_tools)
        except openai.BadRequestError as e:
            if not _is_context_length_error(e):
                raise
            log_event(
                logger,
                "context_overflow_retry",
                max_input_tokens=max_input_tokens,
                message_count=len(messages),
            )
            reduced = max(4096, int(max_input_tokens * 0.85))
            compressed = await compress_messages_if_needed(
                messages,
                llm=self.llm,
                max_input_tokens=reduced,
                query_context=query_context,
            )
            return await self.llm.chat(compressed, tools=kwargs_tools)

    async def process(
        self,
        message: str,
        session_id: str,
        status_callback: Callable[[str, str], Any] | None = None,
    ) -> str:
        """Process a user message and return the assistant's response."""
        with log_duration(logger, "agent_process", session_id=session_id):
            try:
                return await self._process_inner(message, session_id, status_callback)
            except openai.BadRequestError as e:
                if _is_context_length_error(e):
                    log_event(logger, "context_overflow_fatal", session_id=session_id)
                    return (
                        "I hit the model's context limit after many tool calls (often large "
                        "queue/API dumps from Code Mode). Try a narrower question, or ask me to "
                        "check fewer items at a time."
                    )
                raise

    async def _process_inner(self, message: str, session_id: str,
                              status_callback: Callable[[str, str], Any] | None = None) -> str:
        # 1. Save user message
        self.memory.save_message(session_id, "user", message)

        # 2. Retrieve relevant memories
        memories = self.memory.search(message, top_k=self.config.memory.top_k)
        summary = self.memory.get_session_summary(session_id)

        # 3. Build prompt
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        trackers_section = ""
        if self.config.trackers.enabled:
            tb = _format_active_trackers_block(self.memory, self.config.trackers)
            if tb:
                trackers_section = tb + "\n\n"
        system = _build_system_prompt(
            memories, summary, now,
            self.config.agent.workspace,
            self.config.agent.skills_path,
            learning=self.config.learning,
            trackers_section=trackers_section,
        )
        recent = self.memory.get_recent_messages(
            session_id, limit=self.config.agent.recent_messages_limit
        )

        # 4. Tools for the LLM: native builtins plus inlined MCP (see ``_build_llm_tools``).
        tools = self._build_llm_tools(session_id)

        # 5. Build message list
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        messages.extend(recent)

        input_budget = compute_max_input_tokens(
            self.config.llm.context_window, self.config.llm.max_tokens, tools,
        )
        tool_result_budget = default_tool_result_token_budget(tools=tools)

        # 6. Call LLM
        messages = await compress_messages_if_needed(
            messages,
            llm=self.llm,
            max_input_tokens=input_budget,
            query_context=message,
        )
        response = await self._llm_chat(messages, tools, input_budget, message)

        # 7. Tool call loop
        rounds = 0
        total_native_tool_calls = 0
        had_tool_error = False
        while response.has_tool_calls() and rounds < self.max_tool_rounds:
            rounds += 1

            # Append assistant message with tool calls
            assistant_msg: dict[str, Any] = {"role": "assistant", "content": response.content or ""}
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in response.tool_calls
            ]
            messages.append(assistant_msg)

            # Execute each tool call
            for tc in response.tool_calls:
                log_event(logger, "tool_executing", tool=tc.name, session_id=session_id)
                # Skill-learning heuristic: only *native* builtins count (not inlined MCP).
                if is_native_tool(tc.name):
                    total_native_tool_calls += 1

                if status_callback is not None:
                    try:
                        ret = status_callback(tc.name, tc.arguments)
                        if inspect.isawaitable(ret):
                            await ret
                    except Exception:
                        logger.debug("Status callback failed", exc_info=True)

                try:
                    if is_native_tool(tc.name):
                        result = await call_native_tool(
                            tc.name, tc.arguments,
                            context=message, llm=self.llm,
                        )
                    else:
                        parsed = _coerce_tool_arguments(tc.arguments)
                        sig = f"{tc.name}:{json.dumps(parsed, sort_keys=True, default=str)}"
                        guard = self._mcp_repeat_guard.setdefault(
                            session_id, {"sig": "", "fail_streak": 0}
                        )
                        if guard.get("sig") == sig and guard.get("fail_streak", 0) >= 2:
                            result = (
                                "Error: [MCP] The same tool with the same arguments failed twice in a row with "
                                "an MCP-level error (often invalid parameters vs the tool schema). "
                                f"Do not call `{tc.name}` again with these arguments; check logs or fix "
                                "the parameter types (e.g. use scalar fields, not a nested `payload` object)."
                            )
                            mcp_is_err = True
                        else:
                            result, mcp_is_err = await execute_mcp_tool(tc.name, parsed)
                            if mcp_is_err:
                                if guard.get("sig") == sig:
                                    guard["fail_streak"] = guard.get("fail_streak", 0) + 1
                                else:
                                    guard["sig"] = sig
                                    guard["fail_streak"] = 1
                            else:
                                guard["sig"] = ""
                                guard["fail_streak"] = 0
                except Exception as e:
                    result = f"Tool error: {e}"
                    logger.exception(f"Tool call failed: {tc.name}")

                result = verify_tool_result(tc.name, result)
                result = await compress_text_if_needed(
                    result,
                    llm=self.llm,
                    query_context=message,
                    max_output_tokens=tool_result_budget,
                    source=tc.name,
                )
                if (
                    "Error" in result
                    or result.startswith("Tool error")
                    or result.startswith("Error:")
                    or result.startswith("Blocked:")
                ):
                    had_tool_error = True
                if tc.name == "load_skill" and is_native_tool(tc.name):
                    try:
                        args = json.loads(tc.arguments) if isinstance(tc.arguments, str) else tc.arguments
                        sk = str(args.get("name", "")).strip()
                        if sk:
                            outcome = "failure" if "Error" in result else "success"
                            self.memory.record_skill_usage(sk, session_id, outcome)
                    except (json.JSONDecodeError, TypeError):
                        pass
                if self.tool_callback is not None:
                    self.tool_callback(tc.name, tc.arguments, result)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

            # Call LLM again with tool results
            messages = await compress_messages_if_needed(
                messages,
                llm=self.llm,
                max_input_tokens=input_budget,
                query_context=message,
            )
            response = await self._llm_chat(messages, tools, input_budget, message)

        if rounds >= self.max_tool_rounds:
            log_event(logger, "tool_loop_limit", session_id=session_id, rounds=rounds)
            # Ask the LLM to wrap up without tools
            messages.append({
                "role": "user",
                "content": "You have reached the tool call limit. Please respond to the user with what you have so far. Do not call any more tools.",
            })
            messages = await compress_messages_if_needed(
                messages,
                llm=self.llm,
                max_input_tokens=input_budget,
                query_context=message,
            )
            response = await self._llm_chat(messages, None, input_budget, message)

        # 8. Thinking retry — model produced only reasoning after tool use
        content = response.content
        if not content and rounds > 0 and response.reasoning_content:
            log_event(logger, "thinking_retry", session_id=session_id, rounds=rounds,
                      reasoning_len=len(response.reasoning_content))
            messages.append({
                "role": "user",
                "content": (
                    "You used tools and got results, but your last response was empty. "
                    "Please summarize what you found and answer the user's question."
                ),
            })
            messages = await compress_messages_if_needed(
                messages,
                llm=self.llm,
                max_input_tokens=input_budget,
                query_context=message,
            )
            response = await self._llm_chat(messages, None, input_budget, message)
            content = response.content

        content = content or "(no response)"

        # 9. Save assistant response
        self.memory.save_message(session_id, "assistant", content)

        # 10. Periodic maintenance
        if self.memory.should_summarize(session_id):
            try:
                await self.memory.summarize_and_extract(session_id, self.llm)
            except Exception:
                logger.exception("Background summarization failed")

        # 11. Optional skill learning — propose-first, human-in-the-loop.
        # The agent NEVER builds a skill on its own; it writes a proposal,
        # persists a durable row in pending_approvals, and notifies the admin.
        # The admin's reply (possibly after a restart) drives the actual build
        # via handle_skill_decision.
        if self.config.learning.enabled:
            try:
                await self._skill_learner.maybe_propose_skill(
                    session_id,
                    message,
                    content,
                    total_native_tool_calls,
                    had_tool_error,
                    self.llm,
                    memory=self.memory,
                    recipient=self.config.signal.admin_group_id,
                )
            except Exception:
                logger.exception("Skill proposal failed")

        log_event(logger, "agent_response", session_id=session_id,
                  memory_hits=len(memories), tool_rounds=rounds)
        return content

    # --------------------------------------------------- skill approval state

    async def sweep_pending_approvals(self, *, reminder: bool = True) -> tuple[int, int]:
        """Expire timed-out skill proposals and optionally re-ping admins.

        Intended for periodic sweeps during normal operation. For
        restart-recovery use :meth:`recover_pending_approvals` instead so
        the admin sees a single consolidated message covering everything
        that was outstanding.
        """
        try:
            expired, reminded = await self._skill_learner.sweep_expired_approvals(
                self.memory, reminder=reminder,
            )
        except Exception:
            logger.exception("pending approvals sweep failed")
            return 0, 0
        log_event(logger, "pending_approvals_swept", expired=expired, reminded=reminded)
        return expired, reminded

    async def recover_pending_approvals(self) -> tuple[int, int, int]:
        """Restart-recovery entrypoint.

        Presents the admin with ALL outstanding skill approvals that were
        waiting when the agent came back up:

        - **still-pending** items require a decision,
        - **expired-while-down** items are mentioned for awareness only,
        - **approved-but-unbuilt** items schedule a grace-window build that
          the admin can cancel with ``stop <slug>`` / ``cancel <slug>``.

        Returns ``(still_pending_count, expired_while_down_count,
        approved_unbuilt_count)``.
        """
        try:
            still_pending, expired, orphans = await self._skill_learner.run_startup_recovery(
                self.memory, llm=self.llm,
            )
        except Exception:
            logger.exception("pending approvals recovery failed")
            return 0, 0, 0
        log_event(
            logger,
            "pending_approvals_recovered",
            still_pending=len(still_pending),
            expired_while_down=len(expired),
            approved_unbuilt=len(orphans),
        )
        return len(still_pending), len(expired), len(orphans)

    async def cancel_approved_build(self, slug: str) -> bool:
        """Abort an approved-but-unbuilt skill during its grace window."""
        return self._skill_learner.cancel_approved_build(slug, self.memory)

    # ---------------------------------------------------------- skill review

    async def run_skill_review(self, *, notify: bool = True) -> Path | None:
        """One-shot skill review. Safe to call manually or from a systemd timer."""
        log_event(logger, "skill_review_started", notify=notify)
        report = await self._skill_learner.review_skills(self.memory, self.llm, notify=notify)
        log_event(logger, "skill_review_finished", report=str(report) if report else None)
        return report

    def start_skill_review_loop(self) -> None:
        """Spawn a background task that periodically runs ``run_skill_review``."""
        if not self.config.learning.enabled:
            return
        if self._review_task is not None and not self._review_task.done():
            return

        interval = max(1, int(self.config.learning.review_interval_hours)) * 3600
        startup_delay = max(0, int(self.config.learning.review_startup_delay_seconds))

        async def _loop() -> None:
            try:
                if startup_delay > 0:
                    await asyncio.sleep(startup_delay)
                while True:
                    try:
                        await self.run_skill_review(notify=True)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("Periodic skill review failed")
                    await asyncio.sleep(interval)
            except asyncio.CancelledError:
                log_event(logger, "skill_review_loop_cancelled")
                raise

        self._review_task = asyncio.create_task(_loop(), name="skill-review-loop")
        log_event(logger, "skill_review_loop_started", interval_hours=self.config.learning.review_interval_hours)

    async def stop_skill_review_loop(self) -> None:
        if self._review_task is None:
            return
        self._review_task.cancel()
        try:
            await self._review_task
        except (asyncio.CancelledError, Exception):
            pass
        self._review_task = None

    # ---------------------------------------------------------- trackers

    def _get_tracker_scheduler(self) -> TrackerScheduler | None:
        return self._tracker_scheduler

    def start_trackers_loop(self) -> None:
        """Spawn tracker scheduler (reconcile + per-tracker loops)."""
        if not self.config.trackers.enabled:
            return
        if self._tracker_scheduler is not None:
            return

        async def _exec_codemode(code: str, timeout: int) -> tuple[str, bool]:
            return await execute_mcp_tool(
                "mcp-portal__portal_codemode_execute",
                {"code": code, "timeout_seconds": min(120, max(5, int(timeout)))},
            )

        self._tracker_scheduler = TrackerScheduler(
            self.memory,
            self.config.trackers,
            execute_codemode=_exec_codemode,
            execute_bash=None,
        )

        async def _boot() -> None:
            if self._tracker_scheduler is not None:
                await self._tracker_scheduler.start()

        asyncio.create_task(_boot(), name="tracker-scheduler-boot")
        log_event(logger, "trackers_loop_scheduled")

    async def stop_trackers_loop(self) -> None:
        if self._tracker_scheduler is None:
            return
        await self._tracker_scheduler.stop()
        self._tracker_scheduler = None

    def start_tracker_compaction_loop(self) -> None:
        """Periodic DB compaction for tracker samples/rollups."""
        if not self.config.trackers.enabled:
            return
        if self._tracker_compact_task is not None and not self._tracker_compact_task.done():
            return
        delay = max(0, int(self.config.trackers.compaction_startup_delay_seconds))
        interval = max(3600, int(self.config.trackers.compaction_interval_hours) * 3600)

        async def _loop() -> None:
            try:
                if delay > 0:
                    await asyncio.sleep(delay)
                while True:
                    try:
                        self._tracker_compact_runs += 1
                        vacuum = self._tracker_compact_runs % 7 == 0
                        self.memory.compact_tracker_storage(
                            sample_retention_days=self.config.trackers.sample_retention_days,
                            rollup_retention_days=self.config.trackers.rollup_retention_days,
                            vacuum=vacuum,
                        )
                        log_event(
                            logger,
                            "tracker_compaction_done",
                            runs=self._tracker_compact_runs,
                            vacuum=vacuum,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("tracker compaction failed")
                    await asyncio.sleep(interval)
            except asyncio.CancelledError:
                log_event(logger, "tracker_compaction_loop_cancelled")
                raise

        self._tracker_compact_task = asyncio.create_task(_loop(), name="tracker-compaction-loop")
        log_event(logger, "tracker_compaction_loop_started", interval_hours=self.config.trackers.compaction_interval_hours)

    async def stop_tracker_compaction_loop(self) -> None:
        if self._tracker_compact_task is None:
            return
        self._tracker_compact_task.cancel()
        try:
            await self._tracker_compact_task
        except (asyncio.CancelledError, Exception):
            pass
        self._tracker_compact_task = None

    def tracker_recovery_digest(self, *, recipient: str | None = None) -> str:
        """Text block for startup recovery (CLI/Signal)."""
        return format_tracker_recovery_message(self.memory, recipient=recipient)

    async def run_tracker_compaction_once(self, *, vacuum: bool = False) -> dict[str, int]:
        """One-shot compaction (CLI / operator)."""
        return self.memory.compact_tracker_storage(
            sample_retention_days=self.config.trackers.sample_retention_days,
            rollup_retention_days=self.config.trackers.rollup_retention_days,
            vacuum=vacuum,
        )
