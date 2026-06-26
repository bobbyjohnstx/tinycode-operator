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
| **Core Configuration** | | |
| `TINYCODE_SERVER_PASSWORD` | *(none — unauthenticated)* | Server auth password |
| `TINYCODE_PORT` | `4096` | Override server port |
| `TINYCODE_SESSION_ID` | *(none)* | Attach to existing session on start |
| `TINYCODE_WORKDIR` | `/projects` | Working directory for projects (fallback: `/home/tinycode`) |
| **LLM Providers** | | |
| `TINYCODE_OLLAMA_HOST` | `http://host.containers.internal:11434` | Ollama endpoint |
| `TINYCODE_VLLM_URL` | *(none)* | vLLM endpoint (bridged to `TINYCODE_VLLM_HOST`) |
| `TINYCODE_VLLM_HOST` | *(none)* | vLLM endpoint (native tinycode env var) |
| `TINYCODE_VLLM_MODEL` | *(none)* | Default model for vLLM (written to config.json) |
| `OPENROUTER_API_KEY` | *(none)* | OpenRouter API key for cost tracking and balance display |
| **GitOps Configuration** | | |
| `TINYCODE_GIT_REPO` | *(none)* | Git repo URL to clone into `/projects` on startup |
| `TINYCODE_GIT_BRANCH` | *(default branch)* | Branch to clone |
| `TINYCODE_GIT_PULL_ON_RESTART` | `false` | Pull latest on restart if repo exists |
| `TINYCODE_GIT_CLONE_TIMEOUT` | `300` | Clone timeout in seconds |
| **Cluster Management** | | |
| `TINYCODE_CLUSTER_ADMIN` | `false` | Enable cluster-admin agent (downloads oc CLI and mounts kubeconfig) |
| `TINYCODE_OC_VERSION` | `stable` | oc CLI version to download (e.g., `4.17` for reproducibility) |
| **Auto-Detection** | | |
| `TINYCODE_AUTO_DETECT` | `true` | Auto-detect Kubernetes environment (disables LSP downloads) |
| `TINYCODE_DISABLE_LSP_DOWNLOAD` | `1` (recommended in containers) | Skip LSP binary auto-download |
| **Operator-Injected** | | |
| `TINYCODE_CONFIG_CONTENT` | *(none)* | Operator-injected config content (not directly set by users) |
| `TINYCODE_DISCOVERY_NAMESPACES` | *(none)* | Comma-separated list of namespaces for service discovery (operator-managed) |
| **Output (Set by Entrypoint)** | | |
| `TINYCODE_CLUSTER_TYPE` | *(auto-detected)* | Set to `openshift` or `kubernetes` when `TINYCODE_CLUSTER_ADMIN=true` |

## Included Tools

**tmux** (v3.4) is compiled from source and included in the runtime image to support the `/swarm` skill — a supervised multi-worker orchestration tool that creates split-screen sessions for distributed task solving.

## Startup Behaviour

The container ENTRYPOINT is `entrypoint.sh` which:
1. Sets `HOME=/home/tinycode` and `SHELL=/bin/sh` (OpenShift compatibility)
2. Validates `TINYCODE_VLLM_MODEL` format (alphanumeric, `/`, `-`, `.` only; max 255 chars; no shell injection)
3. Bridges `TINYCODE_VLLM_URL` → `TINYCODE_VLLM_HOST`
4. Auto-detects Kubernetes environment (sets `TINYCODE_DISABLE_LSP_DOWNLOAD=1` in-cluster)
5. Writes container defaults to `$XDG_CONFIG_HOME/tinycode/config.json` (lowest-priority config)
   - Includes `"model"` field if `TINYCODE_VLLM_MODEL` is set
6. GitOps mode: clones `TINYCODE_GIT_REPO` into `/projects` (or pulls if already exists)
7. Initializes git repo in `/projects` if not present
8. Copies bundled agents/skills from `/opt/tinycode-defaults/` into PVC
9. Downloads `oc` CLI if `TINYCODE_CLUSTER_ADMIN=true`
10. Runs `tinycode web --hostname 0.0.0.0` (or attaches to `TINYCODE_SESSION_ID`)

User config in `tinycode.jsonc` (PVC-persisted) is never overwritten by the entrypoint.

### GitOps Mode Details

When `TINYCODE_GIT_REPO` is set:
- **First run**: Clones the repo into `/projects`
- **Subsequent runs**:
  - If `/projects/.git` exists with a remote URL: skips clone (preserves local changes)
  - If `TINYCODE_GIT_PULL_ON_RESTART=true`: runs `git pull --ff-only`
  - If `/projects/.git` exists but has no remote: deletes `.git` and clones fresh
- **Credentials**: Mount `.git-credentials` or `.netrc` at `/home/tinycode/` for private repos
- **Timeout**: Clone operation times out after `TINYCODE_GIT_CLONE_TIMEOUT` seconds (default 300)

### In-Cluster Auto-Detection

When `KUBERNETES_SERVICE_HOST` is detected (and `TINYCODE_AUTO_DETECT != "false"`):
- Sets `TINYCODE_DISABLE_LSP_DOWNLOAD=1` (air-gapped default)
- Logs vLLM endpoint if configured, otherwise logs "auto-discovery via Kubernetes services"
- Detection can be disabled with `TINYCODE_AUTO_DETECT=false`

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
