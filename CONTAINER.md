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

### Core
| Variable | Default | Description |
|----------|---------|-------------|
| `TINYCODE_SERVER_PASSWORD` | *(none — unauthenticated)* | Server auth password |
| `TINYCODE_PORT` | `4096` | Override server port |
| `TINYCODE_SESSION_ID` | *(none)* | Attach to existing session on start |
| `TINYCODE_WORKDIR` | `/projects` | Working directory for tinycode |

### LLM Providers
| Variable | Default | Description |
|----------|---------|-------------|
| `TINYCODE_OLLAMA_HOST` | `http://host.containers.internal:11434` | Ollama endpoint |
| `TINYCODE_VLLM_URL` | *(none)* | vLLM endpoint (bridged to TINYCODE_VLLM_HOST) |
| `TINYCODE_VLLM_HOST` | *(none)* | vLLM endpoint (native env var) |
| `TINYCODE_VLLM_MODEL` | *(none)* | Default vLLM model (written to config.json) |
| `OPENROUTER_API_KEY` | *(none)* | OpenRouter API key |

### GitOps
| Variable | Default | Description |
|----------|---------|-------------|
| `TINYCODE_GIT_REPO` | *(none)* | Git repository URL to clone into /projects |
| `TINYCODE_GIT_BRANCH` | *(default branch)* | Git branch to clone/pull |
| `TINYCODE_GIT_PULL_ON_RESTART` | `false` | Pull latest on container restart |
| `TINYCODE_GIT_CLONE_TIMEOUT` | `300` | Timeout in seconds for git clone |

### Cluster Management
| Variable | Default | Description |
|----------|---------|-------------|
| `TINYCODE_CLUSTER_ADMIN` | *(none)* | Enable cluster-admin mode (downloads oc CLI) |
| `TINYCODE_OC_VERSION` | `stable` | OpenShift CLI version (stable/latest/fast/candidate/x.y.z) |

### Auto-detection
| Variable | Default | Description |
|----------|---------|-------------|
| `TINYCODE_AUTO_DETECT` | `true` | Enable in-cluster auto-detection |
| `TINYCODE_DISABLE_LSP_DOWNLOAD` | `1` (in-cluster) | Skip LSP binary auto-download |

### Operator-injected (set by tinycode-operator)
| Variable | Default | Description |
|----------|---------|-------------|
| `TINYCODE_CONFIG_CONTENT` | *(none)* | JSON config content (alternative to ConfigMap mount) |
| `TINYCODE_DISCOVERY_NAMESPACES` | *(none)* | Space-separated list of namespaces for vLLM discovery |

### Output (set by entrypoint)
| Variable | Default | Description |
|----------|---------|-------------|
| `TINYCODE_CLUSTER_TYPE` | *(auto-detected)* | Cluster type: `openshift` or `kubernetes` |

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
