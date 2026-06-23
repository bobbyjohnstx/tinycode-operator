# tinycode-operator

An OpenShift Operator that installs and manages **tinycode** AI coding assistant instances on an OpenShift cluster.

## Overview

The operator watches `TinycodeInstance` custom resources and reconciles them by:
1. Selecting the appropriate SecurityContextConstraint based on spec
2. Running `helm upgrade --install` with values derived from the CR
3. Creating an OpenShift Route for external access
4. Updating CR status with the URL and ready condition

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│ OpenShift Cluster                                        │
│                                                          │
│  tinycode-operator-system/                               │
│  ├── Deployment: tinycode-operator-manager (UID 1001)   │
│  └── ServiceAccount: tinycode-operator-manager          │
│                                                          │
│  <user-namespace>/                                       │
│  ├── TinycodeInstance CR (user creates)                  │
│  ├── Deployment: <name>-tinycode  ──→ Pod (UID 1001)    │
│  ├── Service: <name>-tinycode                            │
│  ├── Route: <name>-tinycode  ──→ https://<host>          │
│  ├── PVC: <name>-data (1Gi)                              │
│  └── PVC: <name>-projects (10Gi default)                 │
│                                                          │
│  Cluster-scoped:                                         │
│  ├── CRD: tinycodeinstances.tinycode.dev                 │
│  ├── SCC: tinycode-restricted (default)                  │
│  ├── SCC: tinycode-hostpath (optional)                   │
│  └── SCC: tinycode-shell (optional, requires review)     │
└─────────────────────────────────────────────────────────┘
```

## Prerequisites

- OpenShift 4.12+ (OCP, ROSA, ARO)
- cluster-admin access for installation
- `oc` CLI and `helm` 3.x in PATH
- Operator image built and pushed to a registry accessible to the cluster

## Installation

```bash
# 1. Clone the operator repo
git clone https://github.com/bobbyjohnstx/tinycode-operator
cd tinycode-operator

# 2. Build and push the operator image
make push IMAGE_ORG=yourorg IMAGE_TAG=v0.1.0

# 3. Install on the cluster (cluster-admin required)
make install OPERATOR_IMAGE=quay.io/yourorg/operator:v0.1.0
```

## Creating a TinycodeInstance

### Basic (PVC storage)

```bash
# Create namespace
oc new-project tinycode-dev

# Create password secret
oc create secret generic tinycode-password \
  --from-literal=TINYCODE_SERVER_PASSWORD=mysecretpass \
  -n tinycode-dev

# Apply the CR
oc apply -f config/samples/tinycode_v1alpha1_basic.yaml

# Get the URL
oc get tinycodeinstance my-tinycode -n tinycode-dev -o jsonpath='{.status.url}'
```

### With Ollama (local LLM)

Deploy Ollama separately (or use an existing instance), then set `spec.ollama.host`:

```yaml
spec:
  ollama:
    host: "http://ollama.ollama-system.svc.cluster.local:11434"
```

### With Host Filesystem Access

```yaml
spec:
  storage:
    hostPath:
      path: /home/developer/projects
      readOnly: false
```

> **Security**: Requires cluster-admin to pre-approve the `tinycode-hostpath` SCC binding.
> The operator will bind it automatically if you have permission.

### With Host Shell Execution

```yaml
spec:
  shell:
    enabled: true
```

> **Security**: Requires cluster-admin review. Grants `hostPID` which allows the
> tinycode shell tool to run commands on the host via `nsenter`.
> Only use in dedicated namespaces with trusted users.

## SecurityContextConstraints

Three SCCs are installed, applied automatically based on `spec`:

| SCC | When Used | hostPath | hostPID | Caps |
|-----|-----------|----------|---------|------|
| `tinycode-restricted` | Default | no | no | ALL dropped |
| `tinycode-hostpath` | `spec.storage.hostPath` set | yes | no | ALL dropped |
| `tinycode-shell` | `spec.shell.enabled=true` | no | yes | SYS_PTRACE only |

All SCCs run as UID 1001 (non-root), GID 0, with `allowPrivilegedContainer: false`.

## TinycodeInstance Spec Reference

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `spec.image` | string | `quay.io/bjohns/tinycode-container:latest` | Container image |
| `spec.replicas` | integer | `1` | Number of pods (1–10) |
| `spec.resources.limits.cpu` | string | `2` | CPU limit |
| `spec.resources.limits.memory` | string | `2Gi` | Memory limit |
| `spec.resources.requests.cpu` | string | `200m` | CPU request |
| `spec.resources.requests.memory` | string | `512Mi` | Memory request |
| `spec.storage.dataSize` | string | `1Gi` | PVC size for SQLite DB and config |
| `spec.storage.projectsSize` | string | `10Gi` | PVC size for project workspace |
| `spec.storage.storageClassName` | string | cluster default | StorageClass for PVCs |
| `spec.storage.hostPath.path` | string | — | Absolute host path to mount at `/projects` |
| `spec.storage.hostPath.readOnly` | bool | `false` | Mount host path read-only |
| `spec.hostname` | string | auto | Custom Route hostname |
| `spec.tlsTermination` | string | `edge` | Route TLS mode: `edge`, `passthrough`, or `reencrypt` |
| `spec.ollama.enabled` | bool | `false` | Deploy an Ollama sidecar |
| `spec.ollama.host` | string | — | External Ollama URL (when `enabled` is false) |
| `spec.ollama.models` | array | — | Ollama model names to pre-pull on startup |
| `spec.auth.passwordSecret` | string | — | Secret name containing `TINYCODE_SERVER_PASSWORD` |
| `spec.shell.enabled` | bool | `false` | Enable host shell access (grants `hostPID`) |
| `spec.shell.allowedCommands` | array | — | Restrict shell commands (future admission webhook enforcement) |
| `spec.nodeSelector` | object | — | Node selection constraints |
| `spec.tolerations` | array | — | Pod tolerations |

## Status

```bash
oc get tinycodeinstances -n tinycode-dev

NAME          READY   URL                                   AGE
my-tinycode   True    https://my-tinycode-tinycode-dev...   5m
```

Status phases: `Pending` → `Deploying` → `Running` (or `Failed` / `Terminating`).

## Uninstall

```bash
make uninstall
```

## Project Structure

```
tinycode-operator/
├── Dockerfile                        # Operator container image
├── Makefile                          # Build/install targets
├── README.md
├── CONTAINER.md                      # Container interface contract
├── operator/
│   ├── main.py                       # Operator controller (kopf)
│   └── requirements.txt
├── helm-charts/
│   └── tinycode/                     # Helm chart deployed per CR
│       ├── Chart.yaml
│       ├── values.yaml
│       └── templates/
│           ├── deployment.yaml
│           ├── service.yaml
│           ├── route.yaml
│           ├── serviceaccount.yaml
│           └── pvc.yaml
├── config/
│   ├── crd/                          # CRD definition
│   ├── rbac/                         # Operator RBAC
│   ├── scc/                          # SecurityContextConstraints
│   ├── manager/                      # Operator Deployment
│   └── samples/                      # Example CRs
├── bundle/
│   └── manifests/                    # OLM ClusterServiceVersion
└── hack/
    ├── install.sh                    # Cluster install script
    ├── uninstall.sh                  # Cluster uninstall script
    └── build-push.sh                 # Image build/push script
```

## Ecosystem

| Project | Description | Repository |
|---------|-------------|------------|
| [tinycode](https://github.com/bobbyjohnstx/tinycode) | Core AI coding assistant — server, TUI, web UI | `github.com/bobbyjohnstx/tinycode` |
| [tinycode-container](https://github.com/bobbyjohnstx/tinycode-container) | Container image packaging tinycode + oh-my-tiny | `github.com/bobbyjohnstx/tinycode-container` |
