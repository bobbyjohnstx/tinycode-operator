#!/usr/bin/env bash
# build-push.sh — Build and push the operator container image.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

IMAGE_REGISTRY="${IMAGE_REGISTRY:-quay.io}"
IMAGE_ORG="${IMAGE_ORG:-tinycode}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
IMAGE="${IMAGE_REGISTRY}/${IMAGE_ORG}/operator:${IMAGE_TAG}"

info() { echo "[INFO]  $*"; }

command -v podman >/dev/null || command -v docker >/dev/null || \
    { echo "[ERROR] podman or docker required" >&2; exit 1; }

CONTAINER_TOOL="podman"
command -v podman >/dev/null || CONTAINER_TOOL="docker"

info "Building image: ${IMAGE}"
"${CONTAINER_TOOL}" build \
    --platform linux/amd64 \
    -t "${IMAGE}" \
    -f "${ROOT_DIR}/Dockerfile" \
    "${ROOT_DIR}"

info "Pushing image: ${IMAGE}"
"${CONTAINER_TOOL}" push "${IMAGE}"

info "✓ Image pushed: ${IMAGE}"
info ""
info "To install with this image:"
info "  OPERATOR_IMAGE=${IMAGE} ./hack/install.sh"
