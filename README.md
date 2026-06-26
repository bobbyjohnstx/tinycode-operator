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
| `spec.storage.projectsAccessMode` | string | `ReadWriteOnce` | Access mode for projects PVC (ReadWriteOnce or ReadWriteMany for multi-replica shared workspaces) |
| `spec.storage.storageClassName` | string | cluster default | StorageClass for PVCs |
| `spec.storage.hostPath.path` | string | — | Absolute path on host node to mount at `/projects` (mutually exclusive with `spec.git.url`) |
| `spec.storage.hostPath.readOnly` | bool | `false` | Mount host path as read-only |
| `spec.hostname` | string | auto | Custom hostname for the tinycode Route |
| `spec.tlsTermination` | string | `edge` | Route TLS mode: `edge`, `passthrough`, or `reencrypt` |
| `spec.ollama.enabled` | bool | `false` | Deploy an Ollama sidecar |
| `spec.ollama.host` | string | — | External Ollama host URL (when `enabled` is false) |
| `spec.ollama.models` | array | — | Ollama model names to pre-pull on startup |
| `spec.auth.passwordSecret` | string | — | Secret name containing `TINYCODE_SERVER_PASSWORD` |
| `spec.shell.enabled` | bool | `false` | Enable host shell access (grants `hostPID` for nsenter-based commands) |
| `spec.shell.allowedCommands` | array | — | Restrict shell commands (future admission webhook enforcement) |
| `spec.nodeSelector` | object | — | Node selection constraints |
| `spec.tolerations` | array | — | Pod tolerations |
| `spec.model` | string | — | Default model ID (e.g., `qwen/Qwen2.5-Coder-32B-Instruct-AWQ`). Written to generated config. |
| `spec.clusterAdmin.enabled` | bool | `false` | Enable cluster-admin mode (mounts kubeconfig, downloads oc CLI) |
| `spec.clusterAdmin.kubeconfigSecretName` | string | — | Secret name containing kubeconfig (required when `enabled=true`) |
| `spec.clusterAdmin.kubeconfigSecretKey` | string | `kubeconfig` | Key within the Secret containing the kubeconfig file |
| `spec.clusterAdmin.ocVersion` | string | `stable` | oc CLI version (e.g., `4.17` for reproducibility) |
| `spec.clusterAdmin.kubeconfigNamespace` | string | — | Namespace where kubeconfig Secret resides (for cross-namespace mounting) |
| `spec.clusterAdmin.clusterRole` | string | — | Auto-provision ServiceAccount with this ClusterRole (cannot be `admin` or `cluster-admin`) |
| `spec.vllm` | array | — | Array of vLLM endpoints to configure as tinycode providers |
| `spec.vllm[].name` | string | — | Provider name (must be unique, lowercase alphanumeric + dashes) |
| `spec.vllm[].url` | string | — | Base URL of vLLM instance (e.g., `http://vllm-qwen.vllm:8000`) |
| `spec.vllm[].models` | object | — | Per-model overrides with `contextLimit` and `outputLimit` (auto-probed if omitted) |
| `spec.discovery.namespaces` | array | — | Namespaces to search for vLLM services (enables cross-namespace discovery) |
| `spec.git.url` | string | — | Git repository URL to clone into `/projects` (validated against URL scheme; mutually exclusive with `spec.storage.hostPath.path`) |
| `spec.git.branch` | string | — | Branch to clone (validated to prevent injection); defaults to repository's default branch |
| `spec.git.credentialsSecret` | string | — | Secret name with git credentials (keys: `username`/`password` for HTTPS, `ssh-privatekey` for SSH) |
| `spec.git.pullOnRestart` | bool | `false` | Pull latest changes from repository on pod restart |
| `spec.git.depth` | integer | `1` | Clone depth (shallow clone by default) |

### CRD Security Constraints

The `TinycodeInstance` CRD enforces validation patterns for security-sensitive fields:

- **Image Registry Restriction** (`spec.image`): Must be from explicitly allowed registries (defaults to quay.io, ghcr.io). Prevents arbitrary image injection.
- **Git URL Validation** (`spec.git.url`): Validated to ensure proper URL format. Only `https://` and `git://` schemes allowed; prevents command injection.
- **Git Branch Validation** (`spec.git.branch`): Alphanumeric, `/`, `-`, `.`, and `_` only; prevents shell injection during clone operations.
- **ClusterRole Allowlist** (`spec.clusterAdmin.clusterRole`): Cannot be `admin`, `cluster-admin`, or any ClusterRole that would escalate privileges. Prevents privilege escalation in cluster-admin mode.
- **SSRF Prevention** (`spec.vllm[].url`): URL validation prevents internal service discovery attacks. Private IP ranges and localhost are allowed by default but can be restricted via NetworkPolicy.

### Security Features

- **NetworkPolicy**: Recommended to restrict ingress/egress traffic to required services
- **Read-Only Root Filesystem**: Can be enabled in PodSecurityPolicy via `readOnlyRootFilesystem: true` for additional hardening
- **Security Context**: All pods run as UID 1001 (non-root) with dropped Linux capabilities (ALL dropped by default)
- **Audit Logging**: Operator actions are logged to cluster audit logs for compliance tracking

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

## Deploying Without the Operator

The tinycode-operator is OpenShift-specific: it creates OpenShift `Route` objects (not standard Ingress) and manages `SecurityContextConstraints` (SCCs), which are OpenShift-only resources. However, the underlying container image (`tinycode-container`) is portable and runs on any Kubernetes cluster.

### Option 1: Raw Kustomize Manifests

The [tinycode-container](https://github.com/bobbyjohnstx/tinycode-container) repo includes Kustomize manifests for vanilla Kubernetes:

- `k8s/base/` — Deployment, Service, PVC (works on any cluster)
- `k8s/overlays/ingress/` — Adds a standard `Ingress` instead of OpenShift `Route`

Deploy with:

```bash
# Clone the container repo
git clone https://github.com/bobbyjohnstx/tinycode-container
cd tinycode-container

# Deploy base resources + standard Ingress
kubectl apply -k k8s/overlays/ingress
```

This approach is lightweight and requires no CRD or operator installation. You manage Kustomize overlays directly for customization.

### Option 2: Tekton + Argo CD

For a fully Kubernetes-native CI/CD pipeline that replaces both the operator and GitHub Actions:

- **Tekton** — Replaces GitHub Actions. Builds the container image from the ContainerFile and pushes to your registry, triggered by git pushes.
- **Argo CD** — Replaces the operator's deployment and reconciliation. Watches the Kustomize manifests (or a Helm chart) in the tinycode-container repo and auto-syncs changes to the cluster.

Together, Tekton + Argo CD cover everything the operator + GitHub Actions do today. The tradeoff: you lose the `TinycodeInstance` CRD abstraction and instead manage Kustomize overlays or Helm values directly — arguably simpler for single-instance deployments.

### Future Enhancement

Making the operator itself Kubernetes-portable (auto-detecting OpenShift vs vanilla Kubernetes and falling back to `Ingress` when SCCs are unavailable) is a potential enhancement for future releases.

## Multi-User Self-Service Provisioning

**Primary deployment model**: One `TinycodeInstance` CR per user, managed centrally by a cluster-admin in the "Creating a TinycodeInstance" section above.

**Alternative for self-service teams**: Users can provision their own `TinycodeInstance` CRs if your team prefers a self-service model. No code changes to the operator are required — it already watches all namespaces and scopes all resources (Deployments, Services, Routes, PVCs, ServiceAccounts, SCC bindings) by CR name + namespace. Two users in different namespaces get completely isolated resources with no collisions.

### Setup (Cluster-Admin, One-Time)

1. **Create a ClusterRole** granting users permission to manage their own TinycodeInstance CRs:

```yaml
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: tinycode-user-role
rules:
  - apiGroups:
      - tinycode.dev
    resources:
      - tinycodeinstances
    verbs:
      - create
      - get
      - list
      - watch
      - delete
```

Save this as `tinycode-user-role.yaml` and apply once:

```bash
oc apply -f tinycode-user-role.yaml
```

### Per-User Setup (Cluster-Admin or Delegated)

For each user who will self-provision, create a namespace and bind the ClusterRole:

```yaml
---
apiVersion: v1
kind: Namespace
metadata:
  name: tinycode-alice

---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: alice-tinycode-user
  namespace: tinycode-alice
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: tinycode-user-role
subjects:
  - kind: User
    name: alice@example.com
    apiGroup: rbac.authorization.k8s.io
```

Save as `alice-tinycode-rolebinding.yaml` and apply:

```bash
oc apply -f alice-tinycode-rolebinding.yaml
```

The user also needs to create Secrets (password) in their namespace. Grant that permission by adding a namespace Role:

```yaml
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: tinycode-secret-creator
  namespace: tinycode-alice
rules:
  - apiGroups:
      - ""
    resources:
      - secrets
    verbs:
      - create
      - get

---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: alice-secret-creator
  namespace: tinycode-alice
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: tinycode-secret-creator
subjects:
  - kind: User
    name: alice@example.com
    apiGroup: rbac.authorization.k8s.io
```

### User Workflow (Self-Service)

Once the cluster-admin has set up the namespace and RoleBinding, the user can:

```bash
# 1. Create a password secret in their namespace
oc create secret generic tinycode-password \
  --from-literal=TINYCODE_SERVER_PASSWORD=mypassword \
  -n tinycode-alice

# 2. Create their TinycodeInstance CR
oc apply -f - <<EOF
---
apiVersion: tinycode.dev/v1alpha1
kind: TinycodeInstance
metadata:
  name: alice-dev
  namespace: tinycode-alice
spec:
  image: quay.io/bjohns/tinycode-container:latest
  replicas: 1
  resources:
    limits:
      cpu: "2"
      memory: "2Gi"
    requests:
      cpu: "200m"
      memory: "512Mi"
  storage:
    dataSize: "1Gi"
    projectsSize: "10Gi"
  auth:
    passwordSecret: tinycode-password
  tlsTermination: edge
EOF

# 3. Wait for the instance to be ready
oc get tinycodeinstance alice-dev -n tinycode-alice -w

# 4. Get the URL
oc get tinycodeinstance alice-dev -n tinycode-alice -o jsonpath='{.status.url}'
```

### Resource Consumption

Each user gets their own pod, PVCs (data and projects), and OpenShift Route. This is **instance-per-user isolation**, not shared multi-tenancy. Resource consumption scales linearly with users:

- **Default limits**: 2 CPU, 2Gi memory per instance
- **Default requests**: 200m CPU, 512Mi memory per instance
- **Storage**: 1Gi (config/DB) + 10Gi (projects) per instance

For 10 users with default settings, the cluster needs capacity for at least 2 CPU cores and 5Gi memory across all tinycode instances, plus infrastructure and other workloads.

### Cleanup

A user can delete their instance:

```bash
oc delete tinycodeinstance alice-dev -n tinycode-alice
```

Cluster-admin can revoke access by removing the RoleBinding:

```bash
oc delete rolebinding alice-tinycode-user -n tinycode-alice
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
