# Cluster-Admin Agent v1

## Context

tinycode instances deployed by the operator currently have no ability to interact with the Kubernetes/OpenShift cluster they run on. This plan adds a "cluster-admin" agent that gives users an AI-assisted interface for cluster operations via the `oc` CLI, scoped to whatever permissions the user's kubeconfig grants.

Three repositories are touched:
- **tinycode-container** (`/Users/bjohns/projects/tinycode-container`) -- entrypoint and image changes
- **tinycode-operator** (`/Users/bjohns/projects/tinycode-operator`) -- CRD, Helm chart, operator logic
- **tinycode** (`/Users/bjohns/projects/tinycode`) -- agent definition (optional; may be config-injected instead)

## Decisions (from user interview)

| Question | Decision |
|----------|----------|
| curl in image | Add via `microdnf install -y curl` in ContainerFile runtime stage |
| oc binary | Use **static oc binary** from Red Hat mirrors — no shared library issues |
| Download timeout | Readiness probe `initialDelaySeconds: 60` — no configurable CRD field |
| Multiple kubeconfig contexts | Document single-context expectation; operator warns in status if multiple detected |
| Token expiry | Document SA token requirement + operator warns on short-lived tokens + docs provide helper command |
| replicas > 1 | Document the risk, recommend `replicas: 1`, do NOT enforce |
| OpenShift vs k8s | Auto-detect at startup via `oc api-resources`; set `TINYCODE_CLUSTER_TYPE` env var; prompt adapts |

## Work Objectives

- A user can set `clusterAdmin.enabled: true` and provide a kubeconfig Secret in their `TinycodeInstance` CR
- The deployed container downloads the static `oc` binary at startup and mounts the kubeconfig
- Entrypoint auto-detects cluster type (OpenShift vs k8s) and sets `TINYCODE_CLUSTER_TYPE`
- A "cluster-admin" tinycode agent is available in the session with `bash: "ask"` permissions
- The agent prompt adapts to cluster type — full OpenShift language on RHOAI, generic k8s elsewhere
- Operator validates the Secret and warns on multiple contexts or short-lived tokens via status conditions

## Guardrails

**Must Have:**
- kubeconfig Secret validation in the operator before Helm install
- `oc` download failure must NOT prevent the container from starting (degraded mode, log warning)
- `KUBECONFIG` env var set to the mounted path so `oc` finds it automatically
- Agent only appears in the agent list when cluster-admin is enabled
- CRD schema designed with v2 forward compatibility (`clusterAdmin.mode` field reserved)

**Must NOT Have:**
- Operator auto-creating ServiceAccounts or ClusterRoleBindings (deferred to v2)
- Pre-baked `oc` in the container image (deferred to v2)
- Custom tools beyond the bash tool for cluster interaction
- `bash: "allow"` -- all bash commands must go through the `ask` permission gate

## Task Flow

### Step 1: Extend the CRD with `clusterAdmin` section

Add a `clusterAdmin` object to the `TinycodeInstance` spec in `config/crd/tinycode.dev_tinycodeinstances.yaml`.

**Fields:**
```yaml
clusterAdmin:
  enabled: false                          # boolean, default false
  kubeconfigSecretName: ""                # required when enabled; Secret in same namespace
  kubeconfigSecretKey: "kubeconfig"       # key inside the Secret, default "kubeconfig"
  ocVersion: "stable"                     # oc release channel/version, default "stable"
```

Reserve `mode` in the description/comments for v2 (`kubeconfig` vs `serviceAccount`), but do not add it as a field yet to keep the schema simple.

**Files:**
- `config/crd/tinycode.dev_tinycodeinstances.yaml` -- add `clusterAdmin` to spec properties with validation (kubeconfigSecretName required when enabled)

**Acceptance:**
- `kubectl apply` the CRD succeeds
- A CR with `clusterAdmin.enabled: true` and `kubeconfigSecretName: "test"` passes validation
- A CR with `clusterAdmin.enabled: true` but no `kubeconfigSecretName` is accepted (operator validates at reconcile time, not CRD level, since CRD conditional required is limited)

---

### Step 2: Update Helm chart to mount kubeconfig and inject agent config

Modify the Helm chart templates so that when `clusterAdmin.enabled` is true:
1. The kubeconfig Secret is mounted as a read-only volume at `/home/tinycode/.kube/config`
2. `KUBECONFIG` env var is set to `/home/tinycode/.kube/config`
3. `TINYCODE_CLUSTER_ADMIN=true` env var signals the entrypoint to download `oc`
4. `TINYCODE_OC_VERSION` env var passes the desired oc version to the entrypoint
5. The cluster-admin agent definition is injected via `TINYCODE_CONFIG_CONTENT` env var (sourced from a ConfigMap rendered by the chart)
6. Startup probe is added (or liveness `initialDelaySeconds` increased to 120s) to accommodate `oc` download time

**Files:**
- `helm-charts/tinycode/templates/deployment.yaml` -- volume mount, env vars, probe adjustment
- `helm-charts/tinycode/templates/configmap.yaml` -- new template; ConfigMap containing the agent config JSON for `TINYCODE_CONFIG_CONTENT`
- `helm-charts/tinycode/values.yaml` -- add `clusterAdmin` section with defaults

**Acceptance:**
- `helm template` with `clusterAdmin.enabled=true` produces a Deployment with the kubeconfig volume mount, correct env vars, and the ConfigMap reference
- `helm template` with `clusterAdmin.enabled=false` (default) produces a Deployment with no kubeconfig volume, no cluster-admin env vars, no ConfigMap
- The ConfigMap contains valid JSON with an agent definition including `bash: "ask"` permission

---

### Step 3: Modify entrypoint.sh to conditionally download `oc`

Add a conditional block to `entrypoint.sh` in tinycode-container that downloads and installs the `oc` binary when `TINYCODE_CLUSTER_ADMIN=true`.

**Key details:**
- Install `oc` to `/home/tinycode/.local/bin/` (writable by UID 1001, add to PATH)
- Download from official Red Hat mirror: `https://mirror.openshift.com/pub/openshift-v4/clients/oc/${OC_VERSION}/linux/oc.tar.gz`
- Detect architecture at runtime (`uname -m` -> `x86_64` or `aarch64`) for correct binary
- `curl` is NOT available in the runtime image -- must use an alternative. Options:
  - Add `microdnf install -y curl` to the ContainerFile runtime stage (preferred, small footprint)
  - Or use Python's `urllib` via a one-liner (Python 3.11 is NOT in the runtime image either)
  - Or add a small static download tool at build time
- Wrap the download in `set +e` / `set -e` so failure logs a warning but does not prevent container startup
- Verify the binary works: `oc version --client` after download
- Log clearly: "Downloading oc CLI..." / "oc CLI installed successfully" / "WARNING: oc CLI download failed, cluster-admin agent will not function"

**Files:**
- `/Users/bjohns/projects/tinycode-container/entrypoint.sh` -- add conditional download block before `exec tinycode`
- `/Users/bjohns/projects/tinycode-container/ContainerFile` -- add `microdnf install -y curl && microdnf clean all` to runtime stage; add `/home/tinycode/.local/bin` to PATH

**Acceptance:**
- Container starts successfully with `TINYCODE_CLUSTER_ADMIN=true` and internet access; `oc version --client` works inside the container
- Container starts successfully with `TINYCODE_CLUSTER_ADMIN=true` and NO internet access; logs a warning but tinycode web still runs
- Container starts successfully with `TINYCODE_CLUSTER_ADMIN` unset; no download attempted, no extra startup time

---

### Step 4: Create the cluster-admin agent definition

Write the agent config JSON that will be injected via `TINYCODE_CONFIG_CONTENT`. This includes the system prompt and permission configuration.

**Agent config structure (inside TINYCODE_CONFIG_CONTENT JSON):**
```json
{
  "agent": {
    "cluster-admin": {
      "description": "Kubernetes/OpenShift cluster administration via oc CLI",
      "mode": "primary",
      "permission": {
        "bash": "ask",
        "read": "allow",
        "glob": "allow",
        "grep": "allow",
        "list": "allow",
        "edit": "deny",
        "write": "deny"
      },
      "prompt": "<system prompt text>"
    }
  }
}
```

**System prompt guidelines:**
- Identity: "You are a Kubernetes/OpenShift cluster administrator"
- Primary tool: `oc` CLI (explain it is available on PATH)
- Read-only commands (get, describe, logs, status, top, api-resources, explain) -- execute freely
- Mutating commands (apply, create, delete, patch, scale, rollout, drain, cordon, taint, adm) -- warn the user before executing, explain what the command will do
- Dangerous commands (delete namespace, delete node, cluster-wide deletes) -- strongly discourage, require explicit confirmation
- Never run `oc login` or attempt to modify the kubeconfig
- If `oc` is not available, inform the user that cluster-admin capability is degraded
- Keep responses focused on cluster operations; do not modify files in the workspace

**Files:**
- Content lives inside the Helm ConfigMap template (`helm-charts/tinycode/templates/configmap.yaml`) from Step 2
- The system prompt may be maintained as a separate file for readability (e.g., `helm-charts/tinycode/prompts/cluster-admin.txt`) and loaded via Helm `{{ .Files.Get }}`, or inlined in the ConfigMap template

**Acceptance:**
- The agent appears in the tinycode agent list when a session starts in a cluster-admin-enabled instance
- The agent does NOT appear when cluster-admin is disabled
- Selecting the agent and asking "list pods in default namespace" results in an `oc get pods -n default` invocation via the bash tool with an ask prompt
- The agent refuses to run `oc delete namespace kube-system` without explicit user confirmation

---

### Step 5: Update operator reconciliation to pass clusterAdmin values to Helm

Modify `operator/main.py` to:
1. Read `clusterAdmin` from the CR spec
2. Validate that the referenced Secret exists and contains the expected key
3. Pass `clusterAdmin.*` values to Helm as chart values
4. Set a status condition `ClusterAdminReady: True/False` with a descriptive message

**Files:**
- `operator/main.py` -- update `reconcile()` to extract clusterAdmin spec, validate Secret, build Helm values, update status

**Acceptance:**
- A CR with `clusterAdmin.enabled: true` and a valid Secret deploys successfully; status shows `ClusterAdminReady: True`
- A CR with `clusterAdmin.enabled: true` and a non-existent Secret sets status `ClusterAdminReady: False` with message "Secret 'X' not found in namespace 'Y'"
- A CR with `clusterAdmin.enabled: true` and a Secret missing the expected key sets status `ClusterAdminReady: False` with message "Key 'kubeconfig' not found in Secret 'X'"
- A CR without `clusterAdmin` (or `enabled: false`) deploys without any cluster-admin resources; no `ClusterAdminReady` condition in status

---

### Step 6: End-to-end validation

Verify the full flow works on a live OpenShift cluster.

**Test procedure:**
1. Create a kubeconfig Secret: `kubectl create secret generic cluster-kubeconfig --from-file=kubeconfig=$HOME/.kube/config`
2. Apply a TinycodeInstance CR with `clusterAdmin.enabled: true, kubeconfigSecretName: "cluster-kubeconfig"`
3. Wait for the pod to become ready
4. Open the tinycode web UI via the Route
5. Select the cluster-admin agent
6. Test read-only: "How many nodes does this cluster have?" -- should run `oc get nodes` and report
7. Test mutating: "Create a namespace called test-ns" -- should warn and ask confirmation before running `oc create namespace test-ns`
8. Clean up: delete the test namespace, delete the CR, delete the Secret

**Acceptance:**
- All 8 test steps pass
- Container startup time is under 2 minutes (including oc download)
- No errors in operator logs during reconciliation
- Status conditions are accurate throughout the lifecycle

## Success Criteria

- A user can go from zero to a working cluster-admin agent by adding 3 lines to their CR spec (`clusterAdmin.enabled`, `kubeconfigSecretName`, and creating the Secret)
- The agent correctly uses `oc` for all cluster interactions
- The `bash: "ask"` permission gate is enforced for every command
- Failure modes are graceful: missing Secret gets a clear status message, failed `oc` download lets the container start in degraded mode
- No changes are required to the tinycode core codebase (agent is config-injected)
