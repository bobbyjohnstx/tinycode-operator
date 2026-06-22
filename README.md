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
│  ├── Deployment: tinycode-operator-manager (UID 1000)   │
│  └── ServiceAccount: tinycode-operator-manager          │
│                                                          │
│  <user-namespace>/                                       │
│  ├── TinycodeInstance CR (user creates)                  │
│  ├── Deployment: <name>-tinycode  ──→ Pod (UID 1000)    │
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
| `tinycode-restricted` | Default | ✗ | ✗ | ALL dropped |
| `tinycode-hostpath` | `spec.storage.hostPath` set | ✓ | ✗ | ALL dropped |
| `tinycode-shell` | `spec.shell.enabled=true` | ✗ | ✓ | SYS_PTRACE only |

All SCCs run as UID 1000 (non-root), with `allowPrivilegedContainer: false`.

## TinycodeInstance Spec Reference

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `spec.image` | string | `quay.io/tinycode/server:latest` | Container image |
| `spec.replicas` | integer | `1` | Number of pods (1–10) |
| `spec.resources` | object | 200m/512Mi → 2/2Gi | CPU/memory limits |
| `spec.storage.dataSize` | string | `1Gi` | PVC size for DB/config |
| `spec.storage.projectsSize` | string | `10Gi` | PVC size for projects |
| `spec.storage.storageClassName` | string | cluster default | StorageClass |
| `spec.storage.hostPath.path` | string | — | Host path to mount at /projects |
| `spec.storage.hostPath.readOnly` | bool | `false` | Read-only host mount |
| `spec.hostname` | string | auto | Custom Route hostname |
| `spec.tlsTermination` | string | `edge` | Route TLS: edge/passthrough/reencrypt |
| `spec.ollama.enabled` | bool | `false` | Deploy Ollama sidecar |
| `spec.ollama.host` | string | — | External Ollama URL |
| `spec.auth.passwordSecret` | string | — | Secret with TINYCODE_SERVER_PASSWORD |
| `spec.shell.enabled` | bool | `false` | Enable host shell access (hostPID) |
| `spec.nodeSelector` | object | — | Node selection constraints |
| `spec.tolerations` | array | — | Pod tolerations |

## Status

```bash
oc get tinycodeinstances -n tinycode-dev

NAME          READY   URL                                   AGE
my-tinycode   True    https://my-tinycode-tinycode-dev...   5m
```

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
