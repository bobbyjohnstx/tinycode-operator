# Container Contract

This file is the authoritative source of truth for the tinycode container interface.
Both `tinycode-container` (which produces the image) and `tinycode-operator` (which deploys it)
must stay in sync with these values.

## Image

| Field | Value |
|-------|-------|
| Registry | `quay.io/bjohns/tinycode-container` |
| Tags | `:latest`, `:<git-sha>` |
| Architectures | `linux/amd64`, `linux/arm64` |
| Base | Red Hat UBI9-minimal |

## Runtime Identity

| Field | Value |
|-------|-------|
| User | `tinycode` |
| UID | `1001` |
| GID | `0` (OpenShift arbitrary-UID pattern: `g=u`) |

## Port

| Field | Value |
|-------|-------|
| Container port | `4096` (tinycode default — `server.ts:123`) |
| Override | `TINYCODE_PORT` env var or `server.port` in config |

## Health Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /global/health` | Liveness + readiness (unauthenticated) |

## Volume Mounts

| Mount path | Purpose | PVC subPath |
|-----------|---------|-------------|
| `/home/tinycode/.local/share/tinycode` | SQLite DB, session history | `data` |
| `/home/tinycode/.config/tinycode` | Config files (config.json, tinycode.jsonc) | `config` |
| `/projects` | User workspace files | *(separate PVC)* |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TINYCODE_SERVER_PASSWORD` | *(none — unauthenticated)* | Server auth password |
| `TINYCODE_OLLAMA_HOST` | `http://host.containers.internal:11434` | Ollama endpoint |
| `TINYCODE_PORT` | `4096` | Override server port |
| `TINYCODE_DISABLE_LSP_DOWNLOAD` | `1` (recommended in containers) | Skip LSP binary auto-download |
| `TINYCODE_SESSION_ID` | *(none)* | Attach to existing session on start |

## Startup Behaviour

The container ENTRYPOINT is `entrypoint.sh` which:
1. Writes container defaults to `$XDG_CONFIG_HOME/tinycode/config.json` (lowest-priority config)
2. Sets `TINYCODE_OLLAMA_HOST` if not already set
3. Runs `tinycode web --hostname 0.0.0.0` (or attaches to `TINYCODE_SESSION_ID`)

User config in `tinycode.jsonc` (PVC-persisted) is never overwritten by the entrypoint.

## XDG Base Directories

| Variable | Value |
|----------|-------|
| `XDG_DATA_HOME` | `/home/tinycode/.local/share` |
| `XDG_CONFIG_HOME` | `/home/tinycode/.config` |
| `XDG_STATE_HOME` | `/home/tinycode/.local/state` |
| `XDG_CACHE_HOME` | `/home/tinycode/.cache` |

## Config Load Order

tinycode merges config from lowest to highest priority:

```
config.json  (written by entrypoint — container defaults)
tinycode.json
tinycode.jsonc  (PVC-persisted — user customisations survive image upgrades)
```

## Repositories

| Project | Purpose | Gitea | GitHub |
|---------|---------|-------|--------|
| `tinycode` | Core server, TUI, CLI | `localhost:3000/bjohns/tinycode` | `github.com/bobbyjohnstx/tinycode` |
| `tinycode-container` | Container image | `localhost:3000/bjohns/tinycode-container` | `github.com/bobbyjohnstx/tinycode-container` |
| `tinycode-operator` | OpenShift Operator | `localhost:3000/bjohns/tinycode-operator` | `github.com/bobbyjohnstx/tinycode-operator` |
