#!/usr/bin/env bash
# install.sh — Install the tinycode operator on an OpenShift cluster.
#
# Prerequisites:
#   - oc CLI logged in as cluster-admin
#   - helm 3.x in PATH
#
# Usage:
#   ./hack/install.sh [--image quay.io/yourorg/operator:tag]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

OPERATOR_IMAGE="${OPERATOR_IMAGE:-quay.io/tinycode/operator:latest}"
OPERATOR_NS="tinycode-operator-system"

info()  { echo "[INFO]  $*"; }
warn()  { echo "[WARN]  $*" >&2; }
error() { echo "[ERROR] $*" >&2; exit 1; }

# ── Preflight checks ──────────────────────────────────────────────────────────

command -v oc   >/dev/null || error "oc not found in PATH"
command -v helm >/dev/null || error "helm not found in PATH"

oc whoami >/dev/null 2>&1 || error "Not logged in to OpenShift. Run: oc login ..."

CURRENT_USER=$(oc whoami)
info "Installing as: ${CURRENT_USER}"

# Verify cluster-admin
if ! oc auth can-i '*' '*' --all-namespaces >/dev/null 2>&1; then
    error "cluster-admin privileges required. Current user: ${CURRENT_USER}"
fi

# ── Install SCCs (cluster-scoped) ─────────────────────────────────────────────

info "Applying SecurityContextConstraints..."
oc apply -f "${ROOT_DIR}/config/scc/tinycode-restricted-scc.yaml"
oc apply -f "${ROOT_DIR}/config/scc/tinycode-hostpath-scc.yaml"
oc apply -f "${ROOT_DIR}/config/scc/tinycode-shell-scc.yaml"

# ── Install CRD ───────────────────────────────────────────────────────────────

info "Applying CRD..."
oc apply -f "${ROOT_DIR}/config/crd/tinycode.dev_tinycodeinstances.yaml"

# Wait for CRD to be established
info "Waiting for CRD to be established..."
oc wait --for=condition=Established \
    crd/tinycodeinstances.tinycode.dev \
    --timeout=60s

# ── Install operator namespace and RBAC ───────────────────────────────────────

info "Creating operator namespace: ${OPERATOR_NS}"
oc apply -f "${ROOT_DIR}/config/manager/manager.yaml"

info "Applying RBAC..."
oc apply -f "${ROOT_DIR}/config/rbac/service_account.yaml"
oc apply -f "${ROOT_DIR}/config/rbac/scc_role.yaml"
oc apply -f "${ROOT_DIR}/config/rbac/role.yaml"
oc apply -f "${ROOT_DIR}/config/rbac/role_binding.yaml"

# ── Deploy operator ───────────────────────────────────────────────────────────

info "Deploying operator manager (image: ${OPERATOR_IMAGE})..."
oc set image deployment/tinycode-operator-manager \
    manager="${OPERATOR_IMAGE}" \
    -n "${OPERATOR_NS}" 2>/dev/null || true

oc rollout status deployment/tinycode-operator-manager \
    -n "${OPERATOR_NS}" \
    --timeout=120s

info "✓ tinycode-operator installed successfully"

# ── vLLM preflight check ──────────────────────────────────────────────────────
# Inspect cluster Deployments for vLLM containers missing tool-calling flags.
# The operator will also re-check at reconcile time and report in CR status.
info ""
info "Checking vLLM deployments for tool calling configuration..."

VLLM_FOUND=false
VLLM_WARN=false

while IFS= read -r ns; do
    while IFS= read -r deploy; do
        # Check if this deployment runs a vLLM container
        args=$(oc get deployment "$deploy" -n "$ns" \
            -o jsonpath='{.spec.template.spec.containers[*].args}' 2>/dev/null)
        echo "$args" | grep -q "\-\-model" || continue

        VLLM_FOUND=true
        has_tool_choice=$(echo "$args" | grep -c "enable-auto-tool-choice" || true)
        has_parser=$(echo "$args" | grep -c "tool-call-parser" || true)

        has_tool_choice=$(echo "$args" | grep -c "enable-auto-tool-choice" || true)
        has_parser=$(echo "$args" | grep -c "tool-call-parser" || true)
        has_fp8=$(echo "$args" | grep -c "kv-cache-dtype" || true)
        max_len=$(echo "$args" | grep -oE 'max-model-len[= ]+[0-9]+' | grep -oE '[0-9]+' || echo "0")

        STATUS="  ✓ ${ns}/${deploy}"
        WARN_PARTS=""

        if [[ "$has_tool_choice" -eq 0 || "$has_parser" -eq 0 ]]; then
            WARN_PARTS="${WARN_PARTS} [tool-calling-missing]"
            VLLM_WARN=true
        fi
        if [[ "$max_len" -gt 0 && "$max_len" -lt 16384 ]]; then
            WARN_PARTS="${WARN_PARTS} [context=${max_len}-too-small]"
            VLLM_WARN=true
        fi
        if [[ "$has_fp8" -eq 0 && "$max_len" -gt 0 ]]; then
            WARN_PARTS="${WARN_PARTS} [fp8-kv-cache-recommended]"
        fi

        if [[ -z "$WARN_PARTS" ]]; then
            info "  ✓ ${ns}/${deploy} — context=${max_len} tool-calling=yes kv-cache-dtype=fp8"
        else
            warn "  ✗ ${ns}/${deploy} —${WARN_PARTS}"
        fi
    done < <(oc get deployments -n "$ns" --no-headers -o custom-columns=NAME:.metadata.name 2>/dev/null)
done < <(oc get namespaces --no-headers -o custom-columns=NAME:.metadata.name 2>/dev/null)

if [[ "$VLLM_FOUND" == "false" ]]; then
    info "  No vLLM deployments found. The operator will check at reconcile time."
fi

if [[ "$VLLM_WARN" == "true" ]]; then
    warn ""
    warn "  ┌──────────────────────────────────────────────────────────────────────────┐"
    warn "  │  ACTION REQUIRED: vLLM not fully configured for tinycode                 │"
    warn "  │                                                                          │"
    warn "  │  Required flags (tool calling):                                          │"
    warn "  │    --enable-auto-tool-choice                                             │"
    warn "  │    --tool-call-parser hermes        (Qwen3/Qwen2.5)                     │"
    warn "  │    --tool-call-parser llama3_json   (Llama 3.x)                         │"
    warn "  │                                                                          │"
    warn "  │  Recommended flags (context window):                                    │"
    warn "  │    --kv-cache-dtype fp8             (halves KV memory — L4/A10/H100)    │"
    warn "  │    --max-model-len 32768            (32k context for coding sessions)    │"
    warn "  │    --gpu-memory-utilization 0.95                                         │"
    warn "  │                                                                          │"
    warn "  │  Full setup guide: docs/rhoai-cluster-setup.md                           │"
    warn "  └──────────────────────────────────────────────────────────────────────────┘"
    warn ""
fi

# ── Next steps ────────────────────────────────────────────────────────────────
info ""
info "Next steps:"
info "  1. Create a namespace for your tinycode instance and grant the operator"
info "     permission to run Helm in it:"
info "     oc new-project tinycode-dev"
info "     oc create rolebinding tinycode-operator-helm \\"
info "       --clusterrole=admin \\"
info "       --serviceaccount=${OPERATOR_NS}:tinycode-operator-manager \\"
info "       -n tinycode-dev"
info ""
info "  2. Create a password secret:"
info "     oc create secret generic tinycode-password \\"
info "       --from-literal=TINYCODE_SERVER_PASSWORD=yourpassword \\"
info "       -n tinycode-dev"
info ""
info "  3. Apply a TinycodeInstance:"
info "     oc apply -f config/samples/tinycode_v1alpha1_basic.yaml"
info ""
info "  4. Get the URL:"
info "     oc get tinycodeinstance -n tinycode-dev -o wide"
info ""
info "  vLLM tool calling: docs/vllm-tool-calling.md"
