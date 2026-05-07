# MCP_Add.md — Adding a New MCP Server to Mose

This guide is the canonical checklist for adding a new MCP (Model Context Protocol)
server to Mose Agent. Follow it end-to-end and the new tools will be reachable
through the Code Mode portal with the correct approval flow, prompt routing,
shell-policy blocks, and tests.

> Audience: developers extending Mose. Treat each section as a required step
> unless it explicitly says "optional".

---

## 0. Decide first: build or adopt?

Before writing anything, decide which of two integration shapes applies.

| Shape | When to pick | Examples |
|---|---|---|
| **A. First-party Python FastMCP sidecar** (`docker/<name>-diagnostics/`) | The upstream service has an HTTP/JSON API but no good off-the-shelf MCP server, OR the available MCP server is unmaintained/unvetted, OR you want full control over the read/write surface | `arr-diagnostics`, `nzbget-diagnostics` |
| **B. Third-party MCP server packaged into a container** (`docker/<name>/`) | A reputable MCP server already exists and you trust it (stars, recent commits, maintainer track record) | `plex-ops-admin` (vladimir-tutin/plex-mcp-server), `plex-stack-automation` (niavasha/plex-mcp-server) |

Decision criteria for "reputable":

- ≥ 50 stars OR a known-good maintainer
- Activity in the last 90 days
- Source is auditable (you can read the code that will be holding your credentials)
- License compatible with your deployment

**If in doubt, build it (Shape A).** A first-party sidecar is ~150 lines for a
typical REST/JSON-RPC backend and inherits all of Mose's plumbing.

The rest of this guide assumes Shape A unless a section is marked
`[Shape B only]`.

---

## 1. Architecture context (where your code plugs in)

```
mose-agent  --stdio-->  mose-mcp-portal  --stdio-->  <your-sidecar>  --HTTP-->  upstream service
                              |                                                       |
                              +----- WebSocket RPC ----- mose-mcp-codemode-sandbox ----+
                              |
                              +----- POST /approve ----- mose-agent (mutation gate)
```

Concretely:

- The **agent** only sees one MCP server: `mcp-portal`. It calls
  `portal_codemode_search` and `portal_codemode_execute`.
- The **portal** loads upstream MCP servers from `mcp_servers.portal.json` at
  startup and proxies them to the Code Mode sandbox via WebSocket RPC.
- Your **sidecar container** is `sleep infinity` by default; the portal
  `docker exec -i`s into it on demand for each MCP request.
- Mutating tools require human approval via the agent's `POST /approve` HTTP
  endpoint (Signal/Discord prompt).

This means there are **five integration surfaces** you have to touch:

1. The sidecar code + Dockerfile (Shape A only)
2. `docker-compose.yml` (service definition + portal `depends_on`)
3. `mcp_servers.portal.json` (operator-local) **and** `mcp_servers.portal.example.json` (committed)
4. `mose/mcp_write_policy.py` (read/write classification for approval)
5. `mose/bash_policy.py`, `mose/agent.py`, `mose/tools.py` (defense-in-depth)

Plus tests, env vars, and docs.

---

## 2. Build the sidecar `[Shape A]`

Mirror `[docker/arr-diagnostics/](docker/arr-diagnostics/)` or
`[docker/nzbget-diagnostics/](docker/nzbget-diagnostics/)` — they are the
reference templates.

### 2.1 Directory layout

```
docker/<name>-diagnostics/
  Dockerfile                          # tini + python:3.12-slim, idle
  requirements.txt                    # httpx + mcp[cli] (same versions as arr-diagnostics)
  mcp-entrypoint-<name>.sh            # exec python -m <name>_diagnostics
  <name>_diagnostics/
    __init__.py
    __main__.py                       # build_app + mcp.run("stdio") + --selftest
    client.py                         # <Name>Client (httpx, auth, transport)
    util.py                           # safe_tool, json_response, redact_config
    mcp_server.py                     # FastMCP("<name>-diagnostics") with all tools
```

### 2.2 Dockerfile

Copy `[docker/arr-diagnostics/Dockerfile](docker/arr-diagnostics/Dockerfile)`
verbatim and change the COPY paths and `WORKDIR`. Keep:

- `FROM python:3.12-slim-bookworm` (matches other sidecars)
- `apt-get install -y --no-install-recommends tini` (clean PID 1)
- `ENTRYPOINT ["/usr/bin/tini", "--"]`
- `CMD ["sleep", "infinity"]` (idle — portal exec's into it on demand)

### 2.3 Client

Synchronous httpx client with auth headers/credentials baked in. **Do not log
the credentials.** Pattern:

```python
class FooClient:
    def __init__(self, host, port, api_key, *, use_https=False, timeout=120.0):
        scheme = "https" if use_https else "http"
        self._base = f"{scheme}://{host}:{port}"
        self._client = httpx.Client(timeout=timeout, headers={"X-Api-Key": api_key})

    def close(self) -> None:
        self._client.close()

    # Keep verbs separated (get_json / post_json / delete_json) so tools read clearly.
```

### 2.4 `util.py`

Copy `safe_tool`, `safe_tool_decorator`, and `json_response` from
`[docker/arr-diagnostics/arr_diagnostics/client.py](docker/arr-diagnostics/arr_diagnostics/client.py)`
(or `nzbget-diagnostics/.../util.py` for a clean copy). These three are the
contract:

- `safe_tool` converts every exception into JSON so a single failure doesn't
  tear down the stdio session.
- `safe_tool_decorator(mcp.tool)` so every tool is wrapped at registration.
- `json_response(data, max_chars=20000)` truncates over-long payloads.

If your backend returns sensitive config blocks (passwords, API keys),
add a `redact_config` helper and call it before `json_response`.

### 2.5 `mcp_server.py`

```python
from mcp.server.fastmcp import FastMCP
from <name>_diagnostics.client import FooClient
from <name>_diagnostics.util import json_response, safe_tool_decorator

def build_foo_app(c: FooClient) -> FastMCP:
    mcp = FastMCP("<name>-diagnostics")
    tool = safe_tool_decorator(mcp.tool)

    @tool()
    def foo_get_status() -> str:
        """Short, LLM-readable description ending in a period."""
        return json_response(c.get_json("/status"))

    # ... one @tool() per operation ...
    return mcp
```

**Tool naming conventions (strict):**

- Prefix every tool with `<name>_` (e.g. `nzbget_status`, `sonarr_get_queue`).
  This namespacing prevents collisions inside the LLM-visible tool list.
- Read tools: verb in the middle (`<name>_get_<thing>`, `<name>_list_<thing>`).
- Mutation tools: action verb (`<name>_delete_<thing>`, `<name>_command_<thing>`,
  `<name>_post_<thing>`).
- Each tool's docstring becomes its description in `portal_codemode_search`.
  Make it specific and end with required parameters mentioned by name.

### 2.6 `__main__.py`

```python
def main() -> None:
    if "--selftest" in sys.argv:
        # Fail fast with clear stderr if env vars missing.
        # Make one read call against the upstream and print "OK <something>".
        ...
        sys.exit(0)

    # Validate required env vars; exit 1 with actionable message if missing.
    client = FooClient(...)
    atexit.register(client.close)
    app = build_foo_app(client)
    app.run(transport="stdio")
```

The `--selftest` is invoked manually for verification (step 7) and is
**required**. Without it you cannot tell whether a connection failure is in
the sidecar or in the portal.

### 2.7 Entrypoint script

```sh
#!/bin/sh
set -e
cd /opt/<name>-diagnostics
exec python -m <name>_diagnostics
```

Make sure it's `chmod +x`'d in the Dockerfile.

---

## 3. Wire it into the stack (BOTH Shapes A and B)

### 3.1 `docker-compose.yml`

Add a service block. For Shape A:

```yaml
  <name>-diagnostics:
    build:
      context: .
      dockerfile: docker/<name>-diagnostics/Dockerfile
    container_name: mose-<name>-diagnostics
    networks: [mose-net]
    environment:
      FOO_HOST: ${FOO_HOST:-}
      FOO_PORT: ${FOO_PORT:-1234}
      FOO_API_KEY: ${FOO_API_KEY:-}
    restart: unless-stopped
```

Then add the new service to `mose-mcp-portal.depends_on`:

```yaml
  mose-mcp-portal:
    depends_on:
      mose-mcp-codemode-sandbox:
        condition: service_started
      <existing sidecars>:
        condition: service_started
      <name>-diagnostics:
        condition: service_started
```

Also update the comment block at the top of `docker-compose.yml` so
operators copy the right `docker compose build/up` invocation.

### 3.2 Portal config — **two files**

This is the #1 mistake when adding a new MCP. The portal reads
**`mcp_servers.portal.json`** (operator-local), not the example file. Both
must be updated:

**A. Update the committed example** so future fresh installs work:
`[mcp_servers.portal.example.json](mcp_servers.portal.example.json)` —
add to `"servers"`:

```json
"<name>-diagnostics": {
  "command": "docker",
  "args": ["exec", "-i", "mose-<name>-diagnostics", "/usr/local/bin/mcp-entrypoint-<name>"],
  "transport": "stdio"
}
```

**B. Update each operator's `mcp_servers.portal.json`** at the root of
their checkout:

```bash
cp mcp_servers.portal.json mcp_servers.portal.json.bak
# Edit and add the same block as above. Don't forget the comma after the
# previous server entry.
python -c "import json; json.load(open('mcp_servers.portal.json')); print('OK')"
```

> The portal's Dockerfile only seeds from `mcp_servers.portal.example.json` if
> `mcp_servers.portal.json` doesn't exist (`if [ ! -f ... ]; then cp ...; fi`).
> On established deployments the operator-local file already exists and
> overrides the example, so the example update alone is invisible until the
> operator-local file is also edited and the image is rebuilt.

### 3.3 `.env.example` and `.env`

Add commented placeholders to `[.env.example](.env.example)` so operators
know which env vars to populate:

```
# Foo MCP sidecar (docker compose: foo-diagnostics).
#FOO_HOST=10.4.251.x
#FOO_PORT=1234
FOO_API_KEY=
```

Keep at least one var (typically the secret) **uncommented** so an operator
opening `.env` for the first time notices it has to be filled in.

Then on the deployment host, edit `.env` to set the real values.

### 3.4 Read/write classification — `mose/mcp_write_policy.py`

Every protected MCP server must list its read-only tools explicitly.
Anything not in the list is treated as a mutation and gated behind the
approval bridge.

```python
# Add to PROTECTED_MCP_SERVERS
PROTECTED_MCP_SERVERS = frozenset({
    ...,
    "<name>-diagnostics",
})

# Define the read allowlist
_FOO_DIAG_READS: frozenset[str] = frozenset({
    "foo_get_status",
    "foo_list_things",
    # ... every read-only tool ...
})

# Register it
_READ_BY_SERVER: dict[str, frozenset[str]] = {
    ...,
    "<name>-diagnostics": _FOO_DIAG_READS,
}
```

**Be conservative.** When in doubt, leave a tool OUT of the read list — that
forces approval, which is the safe default. You can always add it later.

### 3.5 Defense-in-depth — `mose/bash_policy.py`

The LLM might try to reach the backend directly via shell. Block it.

Add the upstream's port + env-var names + container name to
`_BACKEND_TARGET_PATTERNS`:

```python
_BACKEND_TARGET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        ...,
        # curl/wget against the new backend's well-known port
        r"\b(curl|wget|http|httpie)\b[^\n]*?:1234(\b|/)",
        # env vars only the sidecar should have
        r"\$\{?(...|FOO_HOST|FOO_API_KEY)\b",
        # docker exec into the new sidecar
        r"\bdocker\s+exec\b[^\n]*?\bmose-(...|foo-)",
    ]
]
```

Also update `is_backend_target` and `backend_redirect_message` docstrings to
mention the new system, and `bash_rejection_message` if the new system
warrants a tailored hint.

### 3.6 System prompt — `mose/agent.py`

Add the new backend to the **MANDATORY routing** list in `SYSTEM_PROMPT_TEMPLATE`:

```python
## Backend Systems (MANDATORY routing)
Plex, Sonarr, Radarr, NZBGet, Foo, paper_db, ... are reached **only** through Code Mode:
```

And in the warning sentence below it:

```
The shell does not have ``PLEX_TOKEN``, ``...``, ``FOO_API_KEY``, or any other backend credential
```

And in the `mcp-portal__portal_codemode_*` tool description, add an example
of the new server's TS namespace (snake_case): `mcp.foo_diagnostics.foo_get_status`.

### 3.7 Tool description — `mose/tools.py`

Update the `bash` tool's description to mention the new system in the
"DOES NOT support" hint:

```python
"DOES NOT support curl/wget — use ``web_fetch`` for external URLs and ``mcp-portal__portal_codemode_execute`` "
"for backend systems (Plex / Sonarr / Radarr / NZBGet / Foo / paper_db). For state-changing local commands, use ``sre_execute``."
```

---

## 4. Tests

### 4.1 New unit test file: `tests/test_<name>_diagnostics.py`

Mirror `[tests/test_arr_diagnostics.py](tests/test_arr_diagnostics.py)` or
`[tests/test_nzbget_diagnostics.py](tests/test_nzbget_diagnostics.py)`.

Cover at minimum:

- Client builds the right HTTP/RPC payload (auth header present, URL correct,
  positional/keyword params correct).
- Client raises a clean `RuntimeError` on a documented backend error response.
- `safe_tool` returns JSON on `httpx.HTTPStatusError`.
- Each non-trivial helper (e.g. `_editqueue`) has a happy-path and an
  empty-input rejection test.
- `redact_config` masks the right keys (if applicable).
- `build_<name>_app` returns an object with `.run()` (smoke).

The `sys.path.insert` fixture pattern from
`[tests/test_nzbget_diagnostics.py](tests/test_nzbget_diagnostics.py)` is the
canonical way to import a sidecar package whose code lives outside the
top-level `mose*` packages.

### 4.2 Extend `tests/test_mcp_write_policy.py`

```python
@pytest.mark.parametrize(
    "bare,expected",
    [
        ("foo_get_status", "read"),
        ("foo_list_things", "read"),
        ("foo_delete_thing", "write"),
        ("foo_unknown_tool", "write"),     # default-write for safety
    ],
)
def test_<name>_diagnostics(bare: str, expected: str) -> None:
    assert classify_mcp_tool("<name>-diagnostics", bare) == expected
```

Also add an assertion to `test_use_tool_needs_approval` for one read and one
write tool from the new server.

### 4.3 Extend `tests/test_tools.py::TestBackendTargetBlock`

```python
def test_<name>_blocks(self):
    assert is_backend_target('curl http://localhost:1234/api/foo')
    assert is_backend_target('echo $FOO_API_KEY')
    assert is_backend_target("docker exec -i mose-<name>-diagnostics sh")
```

---

## 5. Documentation

### 5.1 `INSTALL.md` — D.7

Append a subsection like the NZBGet one:

```md
**<Name> sidecar (`<name>-diagnostics`):** First-party JSON-RPC MCP in
[`docker/<name>-diagnostics/`](docker/<name>-diagnostics/). Add the service with compose,
set `FOO_HOST` and `FOO_API_KEY` (and optionally `FOO_PORT` / `FOO_USE_HTTPS`)
in `.env` (see `.env.example`). The portal must list `<name>-diagnostics` in
`mcp_servers.portal.json` (seeded from `mcp_servers.portal.example.json`).
From Code Mode, tools are `mcp.<name>_diagnostics.*` (e.g. `<name>_get_status`).

Smoke test from the host (after `docker compose up -d <name>-diagnostics`):

```bash
docker exec mose-<name>-diagnostics python -m <name>_diagnostics --selftest
```
```

Update the prose in D.6 ("which sidecar when") to mention the new system.

### 5.2 `skills/<name>.md`

Write a Code Mode runbook with copy-pasteable TypeScript snippets for the
common operations. Pattern: short heading, one-paragraph "what it does",
fenced `ts` block with `console.log(JSON.stringify(r, null, 2))` so the LLM
sees raw shapes. Mirror `[skills/nzbget.md](skills/nzbget.md)`.

### 5.3 `skills/_overview.md`

Add the new system to the "ReadOnly (default)" sentence so the level_0 prompt
sees it.

---

## 6. Build & deploy procedure

Run on the deployment host:

```bash
# 1. (Operator) Edit .env to set the new backend's credentials.
$EDITOR .env

# 2. (Operator) Edit mcp_servers.portal.json to add the new server entry
#    (NOT just the example file). See section 3.2.
$EDITOR mcp_servers.portal.json
python -c "import json; json.load(open('mcp_servers.portal.json')); print('OK')"

# 3. Build the new sidecar AND rebuild the portal+agent images.
#    Portal+agent must be rebuilt because mcp_servers.portal.json is baked
#    into the image at COPY time, and bash_policy/agent.py changes ship there.
export DOCKER_GID=$(getent group docker | cut -d: -f3)
docker compose build <name>-diagnostics mose-mcp-portal mose-agent

# 4. Recreate the containers. up -d alone won't replace running containers
#    whose images you just changed; --force-recreate guarantees the swap.
docker compose up -d --force-recreate <name>-diagnostics mose-mcp-portal mose-agent
```

> **Why `--force-recreate`:** Compose only auto-recreates if it detects an
> image hash change. After a `--no-cache` build the hash always changes,
> but a regular `build` may produce an image with the same effective hash
> for unrelated services and leave them running on the old image. Passing
> `--force-recreate` removes that ambiguity.

If the portal + agent images appear unchanged after your edits, force a clean
rebuild:

```bash
docker compose build --no-cache mose-mcp-portal mose-agent
docker compose up -d --force-recreate mose-mcp-portal mose-agent
```

---

## 7. Verification (5 checks, all required)

### 7.1 Sidecar can talk to the upstream

```bash
docker exec mose-<name>-diagnostics python -m <name>_diagnostics --selftest
```

Expected: `OK <some-version-or-status-string>` and exit 0. Anything else
means env vars are wrong, network is wrong, or auth is wrong. Fix before
moving on.

### 7.2 Portal config baked correctly

```bash
docker compose exec mose-mcp-portal cat /app/mcp_servers.portal.json | grep -A4 <name>
```

Expected: the new `"<name>-diagnostics"` block. If empty, your operator-local
`mcp_servers.portal.json` was missing the entry, or the build didn't copy
the updated file (check `docker compose build` output for cache hits).

### 7.3 Portal can attach to the sidecar

```bash
docker compose exec mose-mcp-portal sh -c '
python -c "
import asyncio, logging
from pathlib import Path
from mose_portal.aggregator import PortalAggregator
logging.basicConfig(level=logging.INFO, format=\"%(name)s %(levelname)s %(message)s\")
async def main():
    a = PortalAggregator()
    await a.load_servers(Path(\"/app/mcp_servers.portal.json\"))
    for name, srv in a.servers.items():
        print(f\"  CONNECTED {name}: {len(srv.tools)} tools\")
    await a.close()
asyncio.run(main())
"
'
```

Expected: `CONNECTED <name>-diagnostics: <N> tools` for the new server,
plus all previously working servers. If the new one is missing or shows 0
tools, the portal saw the entry but the docker-exec into the sidecar failed
(usually env vars). The exception will be in the printed log lines.

### 7.4 Tool search finds it

In a fresh chat / DM with Mose:

> Use `portal_codemode_search` to find tools matching `<keyword from your domain>`.

Expected: hits with names like `<name>-diagnostics__<name>_get_status` and
copy-pasteable TS examples.

### 7.5 End-to-end read + mutation

> "Show me the status of <thing>" — confirm Mose calls
> `portal_codemode_search` (no shell detour) → `portal_codemode_execute`
> with `mcp.<name>_diagnostics.<name>_get_status({})` → reports the result.

> "Pause/delete/restart <thing>" — confirm a Signal/Discord admin approval
> prompt fires before the mutation runs, and that approval/denial is honored.

---

## 8. Common pitfalls (with fixes)

| Symptom | Root cause | Fix |
|---|---|---|
| `portal_codemode_search` returns no hits for the new system | Operator-local `mcp_servers.portal.json` doesn't include the new entry (only the example does) | Section 3.2.B + rebuild + recreate |
| `docker compose logs mose-mcp-portal` is empty | The portal container runs `sleep infinity`; the real MCP process runs inside the agent's `docker exec` and its logs go to the agent's stderr | `docker compose logs mose-agent` instead, or use the manual loader from 7.3 |
| Image rebuilt but portal still uses old config | `up -d` doesn't replace already-running containers when the image change is layer-cache-equivalent | Add `--force-recreate` to `up -d` |
| Sidecar selftest passes but portal can't connect | Container name mismatch between `docker-compose.yml` (`container_name`) and `mcp_servers.portal.json` (`args[2]`) | Make them identical: `mose-<name>-diagnostics` |
| LLM bypasses Code Mode and tries `curl http://...:1234` | New port/env vars not in `_BACKEND_TARGET_PATTERNS` | Section 3.5 |
| Mutation runs without approval | New server not in `PROTECTED_MCP_SERVERS`, OR a write tool is accidentally listed in the read allowlist | Section 3.4 — be conservative |
| Sidecar exits immediately when portal exec's into it | `__main__.py` checks env vars and exits 1 when missing — `.env` not populated, or compose isn't propagating the var into the container | `docker compose exec <name>-diagnostics env \| grep FOO_` to confirm; fix `.env` and `docker compose up -d --force-recreate <name>-diagnostics` |
| Test imports fail with `ModuleNotFoundError: <name>_diagnostics` | Sidecar package isn't on `sys.path` for tests | Use the `_prepend_<name>_path` fixture pattern from `[tests/test_nzbget_diagnostics.py](tests/test_nzbget_diagnostics.py)` |
| `NZBGET_HOST=localhost` (or any "container-local") doesn't work | The sidecar resolves `localhost` to itself, not the host | Use the host's LAN IP or add `extra_hosts: ["host.docker.internal:host-gateway"]` to the sidecar's compose entry |

---

## 9. Final checklist

Use this as the PR checklist before merging a new MCP integration:

**Code (Shape A only)**
- [ ] `docker/<name>-diagnostics/Dockerfile` (tini, python:3.12-slim, idle CMD)
- [ ] `docker/<name>-diagnostics/requirements.txt` (httpx + mcp[cli])
- [ ] `docker/<name>-diagnostics/mcp-entrypoint-<name>.sh` (chmod +x in Dockerfile)
- [ ] `docker/<name>-diagnostics/<name>_diagnostics/__init__.py`
- [ ] `docker/<name>-diagnostics/<name>_diagnostics/__main__.py` with `--selftest`
- [ ] `docker/<name>-diagnostics/<name>_diagnostics/client.py`
- [ ] `docker/<name>-diagnostics/<name>_diagnostics/util.py` (or copy `safe_tool` inline)
- [ ] `docker/<name>-diagnostics/<name>_diagnostics/mcp_server.py`

**Wiring (always)**
- [ ] `docker-compose.yml` service block + `mose-mcp-portal.depends_on`
- [ ] `docker-compose.yml` build/up comment block updated
- [ ] `mcp_servers.portal.example.json` updated (committed)
- [ ] `mcp_servers.portal.json` updated on the deployment host (operator-local)
- [ ] `.env.example` populated with placeholders
- [ ] `.env` populated on the deployment host (real values)
- [ ] `mose/mcp_write_policy.py` — `PROTECTED_MCP_SERVERS` + `_<NAME>_DIAG_READS` + `_READ_BY_SERVER`
- [ ] `mose/bash_policy.py` — `_BACKEND_TARGET_PATTERNS` (port + env vars + docker exec)
- [ ] `mose/agent.py` — system prompt mandatory routing list and credential mention
- [ ] `mose/tools.py` — `bash` tool description mentions the new system

**Tests**
- [ ] `tests/test_<name>_diagnostics.py` (client + helpers + safe_tool)
- [ ] `tests/test_mcp_write_policy.py` extended (read/write parametrize)
- [ ] `tests/test_tools.py::TestBackendTargetBlock` extended (port + env + docker exec)
- [ ] `pytest` passes

**Docs**
- [ ] `INSTALL.md` D.7 — new sidecar subsection + `which sidecar when` updated
- [ ] `skills/<name>.md` — Code Mode runbook
- [ ] `skills/_overview.md` — backend list updated

**Deploy**
- [ ] `docker compose build <new-service> mose-mcp-portal mose-agent`
- [ ] `docker compose up -d --force-recreate <new-service> mose-mcp-portal mose-agent`
- [ ] All 5 verification checks (section 7) pass
- [ ] Confirmed in chat: read works, mutation prompts for approval

---

## Appendix A — When the upstream isn't HTTP/JSON

If your backend speaks a different protocol (gRPC, WebSocket, raw TCP, a
proprietary binary format, …), the sidecar still works the same way — only
`client.py` changes. The portal, approval flow, and prompt routing don't
care what's behind the sidecar; they only see MCP stdio.

For protocols that don't fit `httpx`, swap the dependency and adjust
`safe_tool` to catch the new transport's exception classes before the
generic `Exception` handler.

## Appendix B — Pure subprocess MCP (no Docker)

Some MCPs ship as a Python module or `npx` command and don't need their own
container (e.g. `paper_db` runs as a workspace-local Python script). For
those, skip section 2 entirely and add to `mcp_servers.portal.json` /
`mcp_servers.portal.example.json` directly:

```json
"foo": {
  "command": "python",
  "args": ["data/workspace/foo/server.py"],
  "transport": "stdio"
}
```

You still need sections 3.2, 3.3 (env), 3.4, 3.5, 3.6, 3.7, tests, and docs.
The portal will spawn the subprocess for each session. **Be careful with
working directory and PYTHONPATH** — the portal cwd is `/app` inside the
container, so subprocess paths must be either absolute or workspace-relative
(volume-mounted at `/app/data/workspace`).
