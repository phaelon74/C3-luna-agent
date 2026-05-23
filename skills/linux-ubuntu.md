# Linux (Ubuntu) — this host only

Commands here apply to the **machine where Mose runs** (agent container host or bare-metal agent). They do **not** reach Plex, Sonarr, Radarr, NZBGet, or other remote APIs — use Code Mode for those (see `_overview.md`).

## Read-only (`bash`, allowlisted)

### Service status

```bash
systemctl status <service> --no-pager
```

### Logs

```bash
journalctl -u <service> --no-pager -n 50
```

### Disk and memory

```bash
df -h
du -sh /path
free -m
```

### Network

```bash
ip a
ss -tlnp
```

### Packages (read)

```bash
apt list --upgradable
```

### System info

```bash
uptime
cat /etc/os-release
```

Common log paths: `/var/log/syslog`, `/var/log/auth.log`, `/var/log/<app>/`.

## Execute (`sre_execute`, approval required)

```bash
systemctl restart <service>
apt update && apt upgrade -y
ufw allow <port>
reboot
```

Restarting a unit (e.g. `plexmediaserver`) only confirms the daemon on **this** box; Plex **content/API** status still requires Code Mode.
