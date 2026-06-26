# Cross-Namespace vLLM Service Discovery

## Context

Tinycode's Kubernetes-native auto-discovery (`local-discovery.ts`) currently only lists vLLM services in the pod's own namespace. On RHOAI clusters, vLLM model serving deployments are typically in separate namespaces (e.g., `qwen3`, `my-first-model`). Users must manually configure endpoints for cross-namespace models. The operator should enable the tinycode pod to discover vLLM services across namespaces.

**Addresses:** tinycode-operator issue #2

**Dependencies:** None (independent from issue #1, but implementing issue #1 first is recommended since it is operator-only while this issue requires tinycode-core changes)

**Cross-repo:** This plan touches both `tinycode-operator` (RBAC, Helm chart) and `tinycode` (`packages/tinycode/src/provider/local-discovery.ts`).

## Work Objectives

- Tinycode pods can discover vLLM services in namespaces other than their own
- RBAC is scoped to minimum necessary (`get`/`list` on services, not cluster-admin)
- Works with RHOAI KServe InferenceService labels automatically
- Discovery scope is configurable per TinycodeInstance via the CR spec

## Guardrails

**Must Have:**
- Configurable namespace list via `spec.discovery.namespaces` in the CRD (Option 2 from the issue, with annotation filtering at runtime)
- An `all` sentinel value for cluster-wide discovery (maps to Option 1)
- ClusterRole for cross-namespace service listing (shared across instances)
- Per-instance ClusterRoleBinding scoped to the instance's ServiceAccount
- Runtime filtering by annotation (`tinycode.dev/discover: vllm`) or label (`serving.kserve.io/inferenceservice`) in `local-discovery.ts` (existing logic, just applied to additional namespaces)

**Must NOT Have:**
- Cluster-admin level permissions (no `*` verbs, no namespace create/delete)
- Multi-cluster support (strictly single-cluster, same-cluster namespaces only)
- Custom CRD watches (no watching InferenceService CRDs from the operator; the tinycode pod discovers at runtime)
- Automatic namespace creation or modification

## Task Flow

### Step 1: Add `spec.discovery` to the CRD

Add a `discovery` section to the TinycodeInstance spec in `config/crd/tinycode.dev_tinycodeinstances.yaml`.

**CRD schema:**
```yaml
discovery:
  description: |
    Configuration for auto-discovery of LLM services across namespaces.
    When omitted, discovery is limited to the instance's own namespace (default behavior).
  type: object
  properties:
    namespaces:
      description: |
        List of namespaces to scan for vLLM services. Services must have the
        annotation tinycode.dev/discover=vllm or the label
        serving.kserve.io/inferenceservice to be discovered. Use ["*"] for
        cluster-wide discovery (requires ClusterRole). If omitted or empty,
        only the instance's own namespace is scanned.
      type: array
      maxItems: 50
      items:
        type: string
```

**Files:** `config/crd/tinycode.dev_tinycodeinstances.yaml`

**Acceptance:**
- A CR with `spec.discovery.namespaces: ["qwen3", "llama"]` passes CRD validation
- A CR with `spec.discovery.namespaces: ["*"]` passes CRD validation
- A CR without `spec.discovery` passes validation (backward compatible)

---

### Step 2: Add ClusterRole and per-instance ClusterRoleBinding to Helm chart

**ClusterRole** (shared, not instance-scoped):
- Create `helm-charts/tinycode/templates/clusterrole.yaml`
- Grants `get` and `list` on services across all namespaces
- Name: `tinycode-cross-namespace-discovery`
- Only rendered when `discovery.namespaces` is non-empty

**ClusterRoleBinding** (per-instance):
- Create `helm-charts/tinycode/templates/clusterrolebinding.yaml`
- Binds the ClusterRole to the instance's ServiceAccount
- Name: `{{ .Values.instanceName }}-{{ .Values.instanceNamespace }}-tinycode-discovery`
- Only rendered when `discovery.namespaces` is non-empty

**Existing namespace-scoped Role/RoleBinding** (`templates/rbac.yaml`):
- Keep unchanged; it still provides same-namespace discovery when cross-namespace is not configured

**Values changes:**
- Add `discovery.namespaces: []` to `values.yaml`

**Files:**
- `helm-charts/tinycode/templates/clusterrole.yaml` -- new
- `helm-charts/tinycode/templates/clusterrolebinding.yaml` -- new
- `helm-charts/tinycode/values.yaml` -- add discovery defaults

**Acceptance:**
- `helm template` with `discovery.namespaces: ["qwen3"]` produces a ClusterRole and ClusterRoleBinding
- `helm template` with `discovery.namespaces: []` does NOT produce ClusterRole or ClusterRoleBinding
- The ClusterRole contains only `get` and `list` verbs on `services` in the core API group
- The ClusterRoleBinding references the correct ServiceAccount name and namespace

---

### Step 3: Pass discovery config from operator to Helm and tinycode pod

**Operator changes (`main.py`):**
- In `helm_values_for_spec()`, extract `spec.discovery.namespaces` and pass as Helm value
- No additional operator-side logic needed; the tinycode pod handles discovery at runtime

**Helm deployment template changes:**
- Add `TINYCODE_DISCOVERY_NAMESPACES` env var to the container spec, set to the comma-separated namespace list
- When the list contains `*`, set the env var to `*`
- When the list is empty (or discovery is not configured), do not set the env var

**Files:**
- `operator/main.py` -- update `helm_values_for_spec()`
- `helm-charts/tinycode/templates/deployment.yaml` -- add env var

**Acceptance:**
- A CR with `spec.discovery.namespaces: ["qwen3", "llama"]` produces a pod with env var `TINYCODE_DISCOVERY_NAMESPACES=qwen3,llama`
- A CR with `spec.discovery.namespaces: ["*"]` produces a pod with env var `TINYCODE_DISCOVERY_NAMESPACES=*`
- A CR without `spec.discovery` produces a pod with no `TINYCODE_DISCOVERY_NAMESPACES` env var

---

### Step 4: Update `local-discovery.ts` to scan configured namespaces

**Changes to `packages/tinycode/src/provider/local-discovery.ts` (in the tinycode repo):**

1. Read `TINYCODE_DISCOVERY_NAMESPACES` env var
2. If set, parse comma-separated list (or `*` for all namespaces)
3. If `*`, call the Kubernetes API to list all namespaces, then list services in each
4. If specific namespaces, list services in each namespace (plus the pod's own namespace)
5. Apply the existing annotation/label filtering logic (`tinycode.dev/discover: vllm` or `serving.kserve.io/inferenceservice`) to all discovered services
6. For services from other namespaces, construct the URL using `<service-name>.<namespace>.svc.cluster.local:<port>` format

**Key details:**
- The Kubernetes API URL for cross-namespace service listing is `GET /api/v1/namespaces/{ns}/services` (per-namespace) or `GET /api/v1/services` (cluster-wide)
- The existing code at line 322 fetches from `/api/v1/namespaces/${namespace}/services`; extend to iterate over configured namespaces
- Provider naming for cross-namespace services: `vllm-<service-name>` (same as today; the namespace is encoded in the URL, not the provider ID)
- The 30-second poll interval (line 13) applies to all configured namespaces

**Files:** `/Users/bjohns/projects/tinycode/packages/tinycode/src/provider/local-discovery.ts`

**Acceptance:**
- Given `TINYCODE_DISCOVERY_NAMESPACES=qwen3,llama` and vLLM services in both namespaces with `tinycode.dev/discover: vllm` annotation, tinycode shows models from both namespaces in the model picker
- Given `TINYCODE_DISCOVERY_NAMESPACES=qwen3` and a service in namespace `llama` (not in the list), that service is NOT discovered
- Given `TINYCODE_DISCOVERY_NAMESPACES=*` and vLLM services across multiple namespaces, all annotated services are discovered
- Given no `TINYCODE_DISCOVERY_NAMESPACES` env var, behavior is unchanged from today (only own namespace)
- Given a service in another namespace without the `tinycode.dev/discover: vllm` annotation or `serving.kserve.io/inferenceservice` label, it is NOT discovered even if the namespace is in the list
- Cross-namespace service URLs use the full `<svc>.<ns>.svc.cluster.local:<port>` format

---

### Step 5: Update RBAC docs, sample CRs, and verify end-to-end

**Update operator RBAC role (`config/rbac/role.yaml`):**
- The operator itself may need `get`/`list` on services across namespaces for its existing `check_vllm_tool_calling()` function (currently namespace-scoped). Check if this is already covered by the ClusterRole at line 55-67 (it lists services but may be scoped). If the operator already has cluster-wide service access, no change needed.

**New sample CR:**
```yaml
# config/samples/tinycode_v1alpha1_discovery.yaml
apiVersion: tinycode.dev/v1alpha1
kind: TinycodeInstance
metadata:
  name: discovery-tinycode
  namespace: tinycode-dev
spec:
  discovery:
    namespaces:
      - qwen3
      - llama
  auth:
    passwordSecret: tinycode-password
```

**Documentation updates:**
- `docs/rhoai-cluster-setup.md` -- add a section on cross-namespace discovery, showing how to set `spec.discovery.namespaces`
- Document the security posture: ClusterRole grants read-only service listing, runtime filtering ensures only annotated services are probed

**End-to-end verification:**
1. Deploy vLLM services in namespaces `qwen3` and `llama` with `tinycode.dev/discover: vllm` annotation
2. Deploy a TinycodeInstance with `spec.discovery.namespaces: ["qwen3", "llama"]`
3. Open tinycode web UI and verify both models appear in the model picker
4. Remove the annotation from one service and verify it disappears within 60 seconds
5. Verify a service in an unlisted namespace is not discovered

**Files:**
- `config/rbac/role.yaml` -- verify/update if needed
- `config/samples/tinycode_v1alpha1_discovery.yaml` -- new
- `docs/rhoai-cluster-setup.md` -- update

**Acceptance:**
- End-to-end flow works: CR with `spec.discovery.namespaces` results in cross-namespace model discovery
- RBAC is minimal: ClusterRole contains only `get`/`list` on services
- Removing an annotation from a service removes it from discovery within one poll cycle
- Documentation accurately describes the setup and security posture

## Success Criteria

- A tinycode instance can discover vLLM models running in different namespaces without manual URL configuration
- RBAC follows least-privilege: only `get`/`list` on services, not broader cluster access
- RHOAI KServe InferenceService services are discovered automatically when in a configured namespace
- Backward compatible: instances without `spec.discovery` behave exactly as before
