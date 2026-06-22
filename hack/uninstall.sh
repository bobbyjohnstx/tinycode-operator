#!/usr/bin/env bash
# uninstall.sh — Remove the tinycode operator from an OpenShift cluster.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
OPERATOR_NS="tinycode-operator-system"

info()  { echo "[INFO]  $*"; }
warn()  { echo "[WARN]  $*" >&2; }

warn "This will remove the tinycode operator and ALL TinycodeInstance resources."
warn "Existing TinycodeInstance deployments in other namespaces will be orphaned."
read -r -p "Continue? [y/N] " confirm
[[ "${confirm}" =~ ^[Yy]$ ]] || { info "Aborted."; exit 0; }

# Delete all TinycodeInstance CRs first (triggers finalizer cleanup)
info "Deleting all TinycodeInstances..."
oc delete tinycodeinstances --all --all-namespaces --timeout=120s 2>/dev/null || true

# Remove operator RBAC and deployment
info "Removing operator resources..."
oc delete -f "${ROOT_DIR}/config/rbac/role_binding.yaml"   2>/dev/null || true
oc delete -f "${ROOT_DIR}/config/rbac/role.yaml"           2>/dev/null || true
oc delete -f "${ROOT_DIR}/config/rbac/scc_role.yaml"       2>/dev/null || true
oc delete -f "${ROOT_DIR}/config/rbac/service_account.yaml" 2>/dev/null || true
oc delete namespace "${OPERATOR_NS}"                         2>/dev/null || true

# Remove CRD (this also removes all CRs)
info "Removing CRD..."
oc delete crd tinycodeinstances.tinycode.dev 2>/dev/null || true

# Remove SCCs (cluster-admin decision whether to keep or remove)
info "Removing SCCs..."
oc delete scc tinycode-restricted tinycode-hostpath tinycode-shell 2>/dev/null || true

info "✓ tinycode-operator uninstalled"
