# Proxmox VE

**No MCP / Code Mode integration** for Proxmox in the current stack. The agent sandbox does **not** have `PROXMOX_API_TOKEN_ID` / `PROXMOX_API_TOKEN_SECRET`, and **`qm` / `pct` / `pvesh` only work on a Proxmox node** — not inside the typical Mose agent container.

## What Mose must not do

- Pretend `qm list` or `pvesh get /nodes` from the agent sandbox will reach your cluster
- `curl` the Proxmox API with token env vars from bash

## What to do instead

1. Ask the operator to use the Proxmox UI or run `qm`/`pct` on the node, or
2. Use **`sre_execute`** with `target_system` describing the Proxmox host when an approved command must run **there** (SSH or API from a bastion).

Future Proxmox MCP would use Code Mode like other backends.

## Reference (operator / future MCP)

- UI: `https://<proxmox-host>:8006/`
- CLI on node: `qm list`, `qm status <vmid>`, `pct list`, `pvesh get /cluster/resources`
- Mutations: start/stop VM, backup, snapshot, migrate — approval required
