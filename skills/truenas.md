# TrueNAS (Storage)

**No MCP / Code Mode integration** for TrueNAS in the current stack. The agent sandbox does **not** have `TRUENAS_API_KEY`.

## What Mose must not do

- `curl` with `Authorization: Bearer $TRUENAS_API_KEY` from bash
- Assume `zpool` / `zfs` on the agent host reflect TrueNAS pools (unless Mose literally runs on the NAS)

## What to do instead

1. Ask the operator to check the TrueNAS UI (**Storage → Pools**, **Shares**, **Alerts**), or
2. Use **`sre_execute`** with approval when a command must run on the NAS or a management host with API/SSH access.

Future TrueNAS MCP sidecars would use Code Mode: `mcp.<server>.<tool>(...)`.

## Reference (operator / future MCP)

- API: `https://<truenas-host>/api/v2.0/`
- Common reads: `pool`, `pool/dataset`, `sharing/smb`, `sharing/nfs`, `disk`, `alert/list`
- Mutations: snapshots, scrub, share changes — always approval-gated
