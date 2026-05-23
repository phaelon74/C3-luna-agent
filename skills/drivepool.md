# DrivePool (StableBit — Windows)

**No MCP / Code Mode integration** for DrivePool. The agent sandbox does **not** have `DRIVEPOOL_HOST`, `DRIVEPOOL_USER`, or `DRIVEPOOL_PASSWORD`, and **pypsrp from the Linux agent container is not supported** in the current design.

## What Mose must not do

- Inline `python3 -c "from pypsrp..."` blocks in bash using `$DRIVEPOOL_*` env vars
- Pool rebalance or add/remove drive without explicit human approval on the Windows side

## What to do instead

1. Ask the operator to run StableBit DrivePool PowerShell on the Windows machine, or
2. Use **`sre_execute`** with a very clear reason and `target_system` when an approved remediation must run on a host that has WinRM/pypsrp access (not from the default sandbox).

Future integration would be an MCP sidecar + Code Mode, not raw remoting from Mose bash.

## Reference (operator)

- WinRM / `Enable-PSRemoting` on the Windows pool host
- StableBit cmdlets (names vary by version): pool status, physical disks, volumes
- Rebalance / add-remove drive: **high risk** — always operator-approved
