# Declarative vLLM Model Config in TinycodeInstance Spec

## Context

vLLM endpoint URLs, model IDs, context limits, and output limits are currently configured by manually exec-ing into the tinycode pod and editing `tinycode.json` on the PVC. This is fragile, not reproducible, and lost on PVC recreation. The operator should own this configuration declaratively through the CRD spec.

**Addresses:** tinycode-operator issue #1

**Dependencies:** None (no tinycode-core changes required)

**Existing pattern to follow:** The cluster-admin feature already uses `TINYCODE_CONFIG_CONTENT` env var injected from a ConfigMap to deliver agent config to tinycode. This plan extends that pattern to deliver vLLM provider config.

## Work Objectives

- Users declare vLLM endpoints and model limits in `spec.vllm` array on TinycodeInstance CR
- Users set the default model via `spec.model`
- Operator generates tinycode-compatible provider config JSON and delivers it via ConfigMap + `TINYCODE_CONFIG_CONTENT` env var
- Context/output limits are auto-detected from `/v1/models` when not explicitly provided
- Pod automatically restarts when vLLM config changes

## Guardrails

**Must Have:**
- `spec.vllm[].name` field for deterministic, user-controlled provider IDs (avoids URL-parsing fragility)
- User-provided `contextLimit`/`outputLimit` are final values passed directly to tinycode config (no further 80/20 split)
- Auto-detected limits apply 80/20 split: `context = floor(max_model_len * 0.8)`, `output = min(4096, floor(max_model_len * 0.2))`
- URL normalization: strip trailing slashes, append `/v1` only if not already present
- Empty `spec.vllm` (or omitted entirely) produces no vLLM config entries and does not interfere with existing manual config or local-discovery
- ConfigMap checksum annotation on Deployment to trigger pod rollout on config changes
- Probe failure for `/v1/models` sets a warning status condition but does not fail the reconcile; the entry is skipped

**Must NOT Have:**
- PVC writes (no init containers or exec); use ConfigMap+env var exclusively
- Disabling local-discovery; operator config and runtime discovery coexist (operator-declared limits take precedence because `TINYCODE_CONFIG_CONTENT` merges after file config, and local-discovery skips providers that already exist)
- More than the `provider` and `model` keys in the generated config JSON; do not expand this into a full tinycode config management system

## Task Flow

### Step 1: Add `spec.vllm` and `spec.model` to the CRD

Add the new fields to `config/crd/tinycode.dev_tinycodeinstances.yaml` under `spec.properties`.

**CRD schema:**
```yaml
model:
  description: |
    Default model identifier in providerID/modelID format
    (e.g., "vllm-qwen3/qwen3-30b"). Sets the "model" key in tinycode config.
    Only meaningful when spec.vllm is non-empty.
  type: string
vllm:
  description: |
    Declarative vLLM endpoint configuration. Each entry becomes a provider
    in tinycode's config. If omitted or empty, no vLLM config is generated
    and existing manual config is preserved.
  type: array
  maxItems: 10
  items:
    type: object
    required:
      - name
      - url
    properties:
      name:
        description: |
          Provider ID used in tinycode's provider registry. This is the prefix
          in model references (e.g., name "vllm-qwen3" => model "vllm-qwen3/qwen3-30b").
          Must be unique across all vllm entries.
        type: string
        pattern: "^[a-z0-9][a-z0-9-]*$"
      url:
        description: |
          Base URL of the vLLM endpoint (e.g., http://qwen3-30b.qwen3.svc.cluster.local:8080).
          The operator appends /v1 for the tinycode baseURL if not already present.
        type: string
        pattern: "^https?://"
      models:
        description: |
          Per-model limit overrides. Keys are model IDs as served by vLLM
          (the --served-model-name value). If a model is not listed here,
          the operator auto-detects limits from /v1/models using an 80/20
          context/output split.
        type: object
        additionalProperties:
          type: object
          properties:
            contextLimit:
              description: Context window token limit (final value, no further split applied).
              type: integer
              minimum: 1024
            outputLimit:
              description: Maximum output tokens (final value, no further split applied).
              type: integer
              minimum: 128
```

**Files:** `config/crd/tinycode.dev_tinycodeinstances.yaml`

**Acceptance:**
- `kubectl apply -f config/crd/` succeeds
- A CR with `spec.vllm: [{name: "vllm-qwen3", url: "http://svc:8080"}]` passes validation
- A CR with `spec.vllm: [{url: "http://svc:8080"}]` (missing required `name`) is rejected
- A CR with `spec.vllm` omitted entirely passes validation
- A CR with more than 10 entries in `spec.vllm` is rejected

---

### Step 2: Implement config generation and `/v1/models` probing in operator

Add two functions to `operator/main.py`:

1. `probe_vllm_models(url, timeout=5)` -- Makes `GET {url}/v1/models`, returns list of `{id, max_model_len}` dicts. Returns empty list on failure (logs warning, does not raise).

2. `build_vllm_config(spec, namespace)` -- Translates `spec.vllm` array into tinycode config JSON:
   - For each entry, creates a provider block with `npm: "@ai-sdk/openai-compatible"` and `options.baseURL`
   - URL normalization: `url.rstrip("/")` then append `/v1` if URL does not already end with `/v1`
   - For each model in the entry's `models` dict, uses provided `contextLimit`/`outputLimit` directly
   - For models discovered via `/v1/models` but NOT in the `models` dict, applies 80/20 auto-split
   - Sets `spec.model` as the top-level `"model"` key if present
   - Returns `(config_dict, warnings_list)` where warnings capture probe failures

**Generated config structure example:**
```json
{
  "model": "vllm-qwen3/qwen3-30b",
  "provider": {
    "vllm-qwen3": {
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "baseURL": "http://qwen3-30b.qwen3.svc.cluster.local:8080/v1",
        "name": "vllm-qwen3"
      },
      "models": {
        "qwen3-30b": {
          "limit": { "context": 26000, "output": 4000 }
        }
      }
    }
  }
}
```

**Files:** `operator/main.py`

**Acceptance:**
- Given a vllm entry with explicit `contextLimit: 7500` and `outputLimit: 500`, the generated config contains `limit.context: 7500` and `limit.output: 500` exactly
- Given a vllm entry with no models dict and a reachable endpoint returning `max_model_len: 32768`, the generated config contains `limit.context: 26214` (floor(32768*0.8)) and `limit.output: 4096` (min(4096, floor(32768*0.2)))
- Given a vllm entry with an unreachable endpoint and no models dict, the entry is skipped with a warning and does not appear in the generated config
- Given `spec.vllm` is empty or omitted, `build_vllm_config` returns `({}, [])`
- URL `http://svc:8080` produces baseURL `http://svc:8080/v1`; URL `http://svc:8080/v1` produces baseURL `http://svc:8080/v1` (no double `/v1`)

---

### Step 3: Deliver config via ConfigMap and update Helm templates

**Operator changes (`main.py`):**
- In `reconcile()`, after building Helm values, call `build_vllm_config(spec, namespace)`
- Merge the vllm config with any existing cluster-admin config (if both features are enabled) into a single `TINYCODE_CONFIG_CONTENT` JSON payload
- Pass the merged config JSON as a new Helm value `configContent`
- Store probe warnings for status conditions

**Helm chart changes:**
- Create `helm-charts/tinycode/templates/configmap.yaml` (or extend the existing cluster-admin ConfigMap if one exists) to render a ConfigMap from `configContent` when non-empty
- In `deployment.yaml`, add/update the `TINYCODE_CONFIG_CONTENT` env var to read from the ConfigMap
- Add a `checksum/config` annotation on the pod template: `checksum/config: {{ include (print $.Template.BasePath "/configmap.yaml") . | sha256sum }}` -- this ensures the Deployment rolls out when config changes

**Helm values changes (`values.yaml`):**
- Add `configContent: ""` default

**Interaction with cluster-admin:** The existing cluster-admin ConfigMap (`{{ .Values.instanceName }}-tinycode-cluster-admin`) already sets `TINYCODE_CONFIG_CONTENT`. The new design should produce a single merged ConfigMap. The operator merges the agent config (from cluster-admin) and the provider config (from vllm) into one JSON payload before passing it to Helm.

**Files:**
- `operator/main.py` -- reconcile loop additions, config merging
- `helm-charts/tinycode/templates/configmap.yaml` -- new or updated template
- `helm-charts/tinycode/templates/deployment.yaml` -- checksum annotation, env var
- `helm-charts/tinycode/values.yaml` -- `configContent` default
- `config/samples/tinycode_v1alpha1_basic.yaml` -- add vllm example

**Acceptance:**
- `helm template` with `configContent` set produces a ConfigMap and a Deployment with `TINYCODE_CONFIG_CONTENT` env var referencing it
- `helm template` with `configContent` empty produces no ConfigMap and no `TINYCODE_CONFIG_CONTENT` env var (unless cluster-admin is also enabled)
- When both `clusterAdmin.enabled` and `spec.vllm` are set, a single ConfigMap contains merged JSON with both `agent` and `provider` keys
- Changing `spec.vllm` on an existing CR triggers a pod rollout (new pod picks up updated config)
- The Deployment pod template has `checksum/config` annotation that changes when ConfigMap content changes

---

### Step 4: Update status conditions and add sample CR

**Status conditions to add/update:**
- `VllmConfigReady`: `True` when all probes succeeded and config was generated; `False` when at least one probe failed (message lists which endpoints failed)
- Existing `VllmWarning` condition continues to report tool-calling issues independently

**New sample CR** (`config/samples/tinycode_v1alpha1_vllm.yaml`):
```yaml
apiVersion: tinycode.dev/v1alpha1
kind: TinycodeInstance
metadata:
  name: vllm-tinycode
  namespace: tinycode-dev
spec:
  model: "vllm-qwen3/qwen3-30b"
  vllm:
    - name: vllm-qwen3
      url: http://qwen3-30b.qwen3.svc.cluster.local:8080
      models:
        qwen3-30b:
          contextLimit: 26000
          outputLimit: 4000
    - name: vllm-llama
      url: http://llama-32-3b.llama.svc.cluster.local:8080
  auth:
    passwordSecret: tinycode-password
  storage:
    dataSize: "1Gi"
    projectsSize: "20Gi"
```

**Files:**
- `operator/main.py` -- update `set_status()` and `reconcile()` for new conditions
- `config/samples/tinycode_v1alpha1_vllm.yaml` -- new sample

**Acceptance:**
- `oc describe tinycodeinstance` shows `VllmConfigReady: True` when all probes succeed
- `oc describe tinycodeinstance` shows `VllmConfigReady: False` with a descriptive message when a probe fails (e.g., "Failed to probe vllm-qwen3 at http://svc:8080/v1/models: connection refused")
- Sample CR applies successfully and produces a working tinycode instance when vLLM services are available

---

### Step 5: Update docs and verify end-to-end

**Update `docs/rhoai-cluster-setup.md`:**
- Replace the manual "exec into pod and edit tinycode.json" section (Section 5) with instructions to set `spec.vllm` on the CR
- Show how `spec.model` selects the default model
- Document the auto-detection behavior for context limits

**End-to-end verification procedure:**
1. Deploy vLLM services with `tinycode.dev/discover: vllm` labels (existing docs)
2. Apply a TinycodeInstance CR with `spec.vllm` pointing to the services
3. Verify the ConfigMap contains correct provider JSON
4. Verify the pod starts and tinycode shows the configured models in the model picker
5. Verify changing `spec.vllm` (add/remove an endpoint) triggers a pod rollout and the new config takes effect
6. Verify `spec.vllm` omitted entirely results in no config generation (manual config on PVC still works)
7. Verify combined `clusterAdmin.enabled + spec.vllm` produces a single merged ConfigMap

**Files:**
- `docs/rhoai-cluster-setup.md` -- update Section 5

**Acceptance:**
- A user can go from zero to configured vLLM models by adding `spec.vllm` to their CR (no exec required)
- Auto-detected limits match the 80/20 split within 1% of manual calculation
- Combined cluster-admin + vllm config works without interference
- Docs accurately reflect the new workflow

## Success Criteria

- Manual exec workflow to edit tinycode.json is fully replaced by `spec.vllm` for the common case
- Config changes via CR updates produce pod rollouts automatically (no manual pod deletion)
- Auto-detection handles the happy path (vLLM reachable) and degrades gracefully (vLLM unreachable)
- Existing tinycode instances without `spec.vllm` continue to work unchanged
