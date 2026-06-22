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
info ""
info "Next steps:"
info "  1. Create a namespace for your tinycode instance:"
info "     oc new-project tinycode-dev"
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
