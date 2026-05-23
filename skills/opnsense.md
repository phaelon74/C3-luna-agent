# OPNsense Firewall

**No MCP / Code Mode integration** for OPNsense in the current stack. The agent sandbox does **not** have `OPNSENSE_API_KEY` or `OPNSENSE_API_SECRET`.

## What Mose must not do

- `curl` / `wget` to `https://<opnsense-host>/api/...` from bash
- Embed `$OPNSENSE_API_KEY` in shell or Python one-liners

Those calls are wrong here and will fail or leak bad habits even if some pattern slipped past allowlists.

## What to do instead

1. Ask the operator to check the OPNsense UI (**System → Log Files**, **Interfaces**, **Firewall → Rules**), or
2. Use **`sre_execute`** with a clear `target_system` and reason when an approved command must run **on a jump host or firewall** that has API/SSH access (not from the agent sandbox pretending it has keys).

If OPNsense is added to the MCP portal later, use Code Mode the same way as Plex/Sonarr: `portal_codemode_search` → `portal_codemode_execute`.

## Reference (operator / future MCP)

- Web UI: `https://<opnsense-host>/`
- API base: `https://<opnsense-host>/api/`
- Typical read endpoints: `/api/core/system/status`, `/api/diagnostics/interface/getInterfaceStatistics`, `/api/firewall/filter/searchRule`
