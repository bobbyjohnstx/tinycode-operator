#!/usr/bin/env bash
# =============================================================================
# rhoai-vllm-setup.sh
#
# PURPOSE
#   Configure vLLM model deployments on an RHOAI cluster for use with
#   tinycode. Run this once per cluster as part of cluster provisioning,
#   before installing the tinycode operator.
#
# WHAT IT DOES
#   1. Patches existing vLLM deployments to enable tool calling
#   2. Increases context window via fp8 KV cache quantization
#   3. Labels services for tinycode Kubernetes auto-discovery
#   4. Verifies the configuration is working
#
# PREREQUISITES
#   - oc CLI authenticated as cluster-admin
#   - vLLM model deployments already running (Qwen3-30B, Llama-3.2-3B)
#   - python3 available locally
#
# USAGE
#   # Dry run (shows what would be changed, makes no changes)
#   DRY_RUN=true ./rhoai-vllm-setup.sh
#
#   # Full run
#   ./rhoai-vllm-setup.sh
#
#   # Override defaults
#   QWEN3_NAMESPACE=my-models \
#   QWEN3_DEPLOYMENT=qwen3 \
#   QWEN3_MAX_LEN=16384 \
#   ./rhoai-vllm-setup.sh
#
# CONFIGURATION (override via environment variables)
#   QWEN3_NAMESPACE    Namespace containing the Qwen3 deployment   [default: qwen3]
#   QWEN3_DEPLOYMENT   Deployment name for Qwen3                   [default: qwen3-30b]
#   QWEN3_SERVICE      Service name for Qwen3                      [default: qwen3-30b]
#   QWEN3_MAX_LEN      max-model-len for Qwen3                     [default: 32768]
#   LLAMA_NAMESPACE    Namespace containing the Llama deployment    [default: auto-detect]
#   LLAMA_DEPLOYMENT   Deployment name for Llama                   [default: auto-detect]
#   LLAMA_SERVICE      Service name for Llama                      [default: auto-detect]
#   DRY_RUN            Set to "true" to preview changes only       [default: false]
#
# CONTEXT WINDOW SIZING
#   The default --max-model-len=32768 (32k tokens) requires fp8 KV cache
#   to fit on a 24GB L4 GPU. If the Qwen3 pod OOMs during startup, reduce:
#     QWEN3_MAX_LEN=16384 ./rhoai-vllm-setup.sh
#
#   After changing max-model-len, update the tinycode config to match:
#     32768 → context: 26000, output: 4000
#     16384 → context: 13000, output: 2000
#
# TOOL CALL PARSER VALUES BY MODEL FAMILY
#   Qwen3, Qwen2.5          → hermes
#   Llama 3.x               → llama3_json
#   Mistral                 → mistral
#   DeepSeek                → deepseek_v2
#   Generic OpenAI-compat   → pythonic
#
# REFERENCES
#   docs/vllm-tool-calling.md     — tool calling flags and verification
#   docs/rhoai-cluster-setup.md   — full cluster setup guide
#   docs/agent-prompt-tiers.md    — tinycode model tier selection
# =============================================================================

set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────────────────

QWEN3_NAMESPACE="${QWEN3_NAMESPACE:-qwen3}"
QWEN3_DEPLOYMENT="${QWEN3_DEPLOYMENT:-qwen3-30b}"
QWEN3_SERVICE="${QWEN3_SERVICE:-qwen3-30b}"
QWEN3_MAX_LEN="${QWEN3_MAX_LEN:-32768}"
QWEN3_TOOL_PARSER="hermes"

LLAMA_NAMESPACE="${LLAMA_NAMESPACE:-}"
LLAMA_DEPLOYMENT="${LLAMA_DEPLOYMENT:-}"
LLAMA_SERVICE="${LLAMA_SERVICE:-}"
LLAMA_TOOL_PARSER="llama3_json"

DRY_RUN="${DRY_RUN:-false}"

# Minimum context window to be useful for tinycode coding sessions.
# Sessions with tool schemas + system prompt need at least this much.
MIN_CONTEXT_TOKENS=16384

# ── Helpers ────────────────────────────────────────────────────────────────────

info()    { echo "[INFO]  $*"; }
warn()    { echo "[WARN]  $*" >&2; }
error()   { echo "[ERROR] $*" >&2; exit 1; }
dry_run() { echo "[DRY]   (skipped) $*"; }

oc_apply() {
  if [[ "$DRY_RUN" == "true" ]]; then
    dry_run "oc $*"
  else
    oc "$@"
  fi
}

# Check whether a deployment already has an arg set
has_arg() {
  local ns=$1 deploy=$2 pattern=$3
  oc get deployment "$deploy" -n "$ns" \
    -o jsonpath='{.spec.template.spec.containers[*].args}' 2>/dev/null \
    | grep -qF "$pattern"
}

# ── Preflight ──────────────────────────────────────────────────────────────────

command -v oc     >/dev/null || error "oc not found in PATH"
command -v python3 >/dev/null || error "python3 not found in PATH"

oc whoami >/dev/null 2>&1 || error "Not logged in. Run: oc login <cluster-url>"

if [[ "$DRY_RUN" == "true" ]]; then
  info "DRY RUN mode — no changes will be made"
fi

info "Cluster: $(oc whoami --show-server)"
info "User:    $(oc whoami)"

# ── Step 1: Auto-detect Llama deployment if not specified ──────────────────────

if [[ -z "$LLAMA_NAMESPACE" || -z "$LLAMA_DEPLOYMENT" ]]; then
  info "Auto-detecting Llama deployment..."
  LLAMA_LINE=$(oc get deployment -A --no-headers 2>/dev/null \
    | grep -i "llama" | grep -v "metrics\|monitor" | head -1) || true

  if [[ -z "$LLAMA_LINE" ]]; then
    warn "No Llama deployment found. Skipping Llama configuration."
    warn "Set LLAMA_NAMESPACE and LLAMA_DEPLOYMENT to configure it manually."
    SKIP_LLAMA=true
  else
    LLAMA_NAMESPACE=$(echo "$LLAMA_LINE" | awk '{print $1}')
    LLAMA_DEPLOYMENT=$(echo "$LLAMA_LINE" | awk '{print $2}')
    info "Found Llama: ${LLAMA_NAMESPACE}/${LLAMA_DEPLOYMENT}"
    SKIP_LLAMA=false
  fi
else
  SKIP_LLAMA=false
fi

# Auto-detect Llama service name if not specified
if [[ "$SKIP_LLAMA" == "false" && -z "$LLAMA_SERVICE" ]]; then
  LLAMA_SERVICE=$(oc get svc -n "$LLAMA_NAMESPACE" --no-headers \
    | grep -i "llama" | grep -v "metrics" | awk '{print $1}' | head -1) || true
  if [[ -z "$LLAMA_SERVICE" ]]; then
    warn "No Llama service found in ${LLAMA_NAMESPACE}. Service labeling skipped."
  fi
fi

# ── Step 2: Verify Qwen3 deployment exists ─────────────────────────────────────

info ""
info "=== Configuring Qwen3 (${QWEN3_NAMESPACE}/${QWEN3_DEPLOYMENT}) ==="

oc get deployment "$QWEN3_DEPLOYMENT" -n "$QWEN3_NAMESPACE" >/dev/null 2>&1 \
  || error "Qwen3 deployment '${QWEN3_DEPLOYMENT}' not found in namespace '${QWEN3_NAMESPACE}'"

# ── Step 3: Add tool calling flags to Qwen3 ───────────────────────────────────

if has_arg "$QWEN3_NAMESPACE" "$QWEN3_DEPLOYMENT" "enable-auto-tool-choice"; then
  info "  Tool calling already enabled — skipping"
else
  info "  Adding --enable-auto-tool-choice --tool-call-parser ${QWEN3_TOOL_PARSER}"
  oc_apply patch deployment "$QWEN3_DEPLOYMENT" -n "$QWEN3_NAMESPACE" \
    --type=json -p="[
      {\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/args/-\",\"value\":\"--enable-auto-tool-choice\"},
      {\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/args/-\",\"value\":\"--tool-call-parser\"},
      {\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/args/-\",\"value\":\"${QWEN3_TOOL_PARSER}\"}
    ]"
fi

# ── Step 4: Add fp8 KV cache and increase context window ──────────────────────

if has_arg "$QWEN3_NAMESPACE" "$QWEN3_DEPLOYMENT" "kv-cache-dtype"; then
  info "  fp8 KV cache already set — skipping"
else
  info "  Adding --kv-cache-dtype fp8 (halves KV cache memory)"
  oc_apply patch deployment "$QWEN3_DEPLOYMENT" -n "$QWEN3_NAMESPACE" \
    --type=json -p='[
      {"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kv-cache-dtype"},
      {"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"fp8"}
    ]'
fi

# Update max-model-len — find and replace existing value
CURRENT_MAX=$(oc get deployment "$QWEN3_DEPLOYMENT" -n "$QWEN3_NAMESPACE" \
  -o jsonpath='{.spec.template.spec.containers[0].args}' 2>/dev/null \
  | python3 -c "import sys,json; args=json.load(sys.stdin); \
    [print(a.split('=')[1]) for a in args if a.startswith('--max-model-len')] or print(0)" \
  2>/dev/null || echo "0")

if [[ "$CURRENT_MAX" == "$QWEN3_MAX_LEN" ]]; then
  info "  max-model-len already set to ${QWEN3_MAX_LEN} — skipping"
else
  info "  Setting --max-model-len=${QWEN3_MAX_LEN} (was ${CURRENT_MAX})"
  # Find the index of the max-model-len arg
  MAX_LEN_IDX=$(oc get deployment "$QWEN3_DEPLOYMENT" -n "$QWEN3_NAMESPACE" \
    -o jsonpath='{.spec.template.spec.containers[0].args}' 2>/dev/null \
    | python3 -c "import sys,json; args=json.load(sys.stdin); \
      idxs=[i for i,a in enumerate(args) if '--max-model-len' in a]; \
      print(idxs[0] if idxs else -1)" 2>/dev/null || echo "-1")

  if [[ "$MAX_LEN_IDX" == "-1" ]]; then
    oc_apply patch deployment "$QWEN3_DEPLOYMENT" -n "$QWEN3_NAMESPACE" \
      --type=json -p="[
        {\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/args/-\",
         \"value\":\"--max-model-len=${QWEN3_MAX_LEN}\"}
      ]"
  else
    oc_apply patch deployment "$QWEN3_DEPLOYMENT" -n "$QWEN3_NAMESPACE" \
      --type=json -p="[
        {\"op\":\"replace\",
         \"path\":\"/spec/template/spec/containers/0/args/${MAX_LEN_IDX}\",
         \"value\":\"--max-model-len=${QWEN3_MAX_LEN}\"}
      ]"
  fi
fi

# Bump gpu-memory-utilization to 0.95 to maximise KV cache space
if has_arg "$QWEN3_NAMESPACE" "$QWEN3_DEPLOYMENT" "gpu-memory-utilization=0.95"; then
  info "  gpu-memory-utilization already at 0.95 — skipping"
else
  UTIL_IDX=$(oc get deployment "$QWEN3_DEPLOYMENT" -n "$QWEN3_NAMESPACE" \
    -o jsonpath='{.spec.template.spec.containers[0].args}' 2>/dev/null \
    | python3 -c "import sys,json; args=json.load(sys.stdin); \
      idxs=[i for i,a in enumerate(args) if 'gpu-memory-utilization' in a]; \
      print(idxs[0] if idxs else -1)" 2>/dev/null || echo "-1")

  if [[ "$UTIL_IDX" == "-1" ]]; then
    info "  Adding --gpu-memory-utilization=0.95"
    oc_apply patch deployment "$QWEN3_DEPLOYMENT" -n "$QWEN3_NAMESPACE" \
      --type=json -p='[
        {"op":"add","path":"/spec/template/spec/containers/0/args/-",
         "value":"--gpu-memory-utilization=0.95"}
      ]'
  else
    info "  Updating --gpu-memory-utilization to 0.95"
    oc_apply patch deployment "$QWEN3_DEPLOYMENT" -n "$QWEN3_NAMESPACE" \
      --type=json -p="[
        {\"op\":\"replace\",
         \"path\":\"/spec/template/spec/containers/0/args/${UTIL_IDX}\",
         \"value\":\"--gpu-memory-utilization=0.95\"}
      ]"
  fi
fi

# Label service for tinycode auto-discovery
if [[ -n "$QWEN3_SERVICE" ]]; then
  info "  Labeling service ${QWEN3_SERVICE} for tinycode auto-discovery"
  oc_apply label service "$QWEN3_SERVICE" -n "$QWEN3_NAMESPACE" \
    tinycode.dev/discover=vllm --overwrite
fi

# ── Step 5: Wait for Qwen3 rollout ────────────────────────────────────────────

if [[ "$DRY_RUN" != "true" ]]; then
  info "  Waiting for Qwen3 rollout (model reload takes 2-4 min)..."
  if ! oc rollout status deployment/"$QWEN3_DEPLOYMENT" -n "$QWEN3_NAMESPACE" \
      --timeout=300s 2>&1; then
    warn "  Rollout timed out or failed. Check pod status:"
    warn "    oc describe pod -n ${QWEN3_NAMESPACE} -l app=${QWEN3_DEPLOYMENT}"
    warn "  If OOMKilled, reduce context window:"
    warn "    QWEN3_MAX_LEN=16384 $0"
  fi
fi

# ── Step 6: Configure Llama ───────────────────────────────────────────────────

if [[ "$SKIP_LLAMA" == "false" ]]; then
  info ""
  info "=== Configuring Llama (${LLAMA_NAMESPACE}/${LLAMA_DEPLOYMENT}) ==="

  if has_arg "$LLAMA_NAMESPACE" "$LLAMA_DEPLOYMENT" "enable-auto-tool-choice"; then
    info "  Tool calling already enabled — skipping"
  else
    info "  Adding --enable-auto-tool-choice --tool-call-parser ${LLAMA_TOOL_PARSER}"
    oc_apply patch deployment "$LLAMA_DEPLOYMENT" -n "$LLAMA_NAMESPACE" \
      --type=json -p="[
        {\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/args/-\",\"value\":\"--enable-auto-tool-choice\"},
        {\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/args/-\",\"value\":\"--tool-call-parser\"},
        {\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/args/-\",\"value\":\"${LLAMA_TOOL_PARSER}\"}
      ]"
  fi

  if [[ -n "$LLAMA_SERVICE" ]]; then
    info "  Labeling service ${LLAMA_SERVICE} for tinycode auto-discovery"
    oc_apply label service "$LLAMA_SERVICE" -n "$LLAMA_NAMESPACE" \
      tinycode.dev/discover=vllm --overwrite
  fi

  if [[ "$DRY_RUN" != "true" ]]; then
    info "  Waiting for Llama rollout..."
    oc rollout status deployment/"$LLAMA_DEPLOYMENT" -n "$LLAMA_NAMESPACE" \
      --timeout=180s || warn "  Llama rollout did not complete cleanly — check pod logs"
  fi
fi

# ── Step 7: Verify ────────────────────────────────────────────────────────────

if [[ "$DRY_RUN" == "true" ]]; then
  info ""
  info "DRY RUN complete. Re-run without DRY_RUN=true to apply changes."
  exit 0
fi

info ""
info "=== Verifying configuration ==="

QWEN3_IP=$(oc get svc "$QWEN3_SERVICE" -n "$QWEN3_NAMESPACE" \
  -o jsonpath='{.spec.clusterIP}' 2>/dev/null || echo "")

if [[ -z "$QWEN3_IP" ]]; then
  warn "Could not get Qwen3 ClusterIP — skipping verification"
  warn "Run manually: oc get svc ${QWEN3_SERVICE} -n ${QWEN3_NAMESPACE}"
else
  # Test tool calling from a temporary pod
  info "  Testing Qwen3 tool calling..."
  TC_RESULT=$(oc run "tinycode-verify-$$" \
    --image=registry.access.redhat.com/ubi9/ubi-minimal:latest \
    --restart=Never --rm -i --command -- \
    curl -sf --max-time 10 \
    -X POST "http://${QWEN3_IP}:8080/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"qwen3-30b\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],
         \"tools\":[{\"type\":\"function\",\"function\":{\"name\":\"t\",\"description\":\"t\",
         \"parameters\":{\"type\":\"object\",\"properties\":{}}}}],
         \"tool_choice\":\"auto\",\"max_tokens\":5}" 2>/dev/null || echo "ERROR")

  if echo "$TC_RESULT" | grep -q "enable-auto-tool-choice"; then
    warn "  ✗ FAIL: Tool calling not enabled on Qwen3"
    warn "    The deployment patch may not have applied. Check:"
    warn "    oc get deployment ${QWEN3_DEPLOYMENT} -n ${QWEN3_NAMESPACE} \\"
    warn "      -o jsonpath='{.spec.template.spec.containers[0].args}'"
  elif echo "$TC_RESULT" | grep -q "ERROR"; then
    warn "  ✗ WARN: Could not reach Qwen3 at ${QWEN3_IP}:8080 — model may still be loading"
  else
    info "  ✓ PASS: Qwen3 tool calling working"
  fi

  # Check context window
  info "  Checking context window..."
  oc run "tinycode-ctx-$$" \
    --image=registry.access.redhat.com/ubi9/ubi-minimal:latest \
    --restart=Never --rm -i --command -- \
    curl -sf --max-time 5 "http://${QWEN3_IP}:8080/v1/models" 2>/dev/null \
  | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    for m in d.get('data', []):
        ctx = m.get('max_model_len', 0)
        ok = ctx >= ${MIN_CONTEXT_TOKENS}
        status = '✓' if ok else '✗'
        note = '' if ok else f' (below recommended ${MIN_CONTEXT_TOKENS})'
        print(f'  {status} {m[\"id\"]}: {ctx} tokens context{note}')
except Exception as e:
    print(f'  Could not parse /v1/models response: {e}')
" || warn "  Could not check context window — model may still be loading"
fi

# ── Step 8: Print tinycode config for the operator ────────────────────────────

QWEN3_SVC_DNS="${QWEN3_SERVICE}.${QWEN3_NAMESPACE}.svc.cluster.local"
LLAMA_SVC_DNS=""
if [[ "$SKIP_LLAMA" == "false" && -n "$LLAMA_SERVICE" ]]; then
  LLAMA_SVC_DNS="${LLAMA_SERVICE}.${LLAMA_NAMESPACE}.svc.cluster.local"
fi

CONTEXT_LIMIT=$(python3 -c "print(int(${QWEN3_MAX_LEN} * 0.80))")
OUTPUT_LIMIT=$(python3 -c "print(min(4096, int(${QWEN3_MAX_LEN} * 0.20)))")

info ""
info "=== Setup complete ==="
info ""
info "After deploying a TinycodeInstance, configure it with:"
info "(This step will be automated by tinycode-operator issue #1)"
info ""
cat <<EOF
POD=\$(oc get pods -n tinycode-dev --no-headers | awk 'NR==1{print \$1}')
oc exec -n tinycode-dev "\$POD" -- sh -c 'cat > /home/tinycode/.config/tinycode/tinycode.json << '"'"'TINYCODE_EOF'"'"'
{
  "model": "vllm-qwen3/qwen3-30b",
  "small_model": "vllm-llama/llama-32-3b-instruct",
  "provider": {
    "vllm-qwen3": {
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "baseURL": "http://${QWEN3_SVC_DNS}:8080/v1",
        "name": "vllm-qwen3"
      },
      "models": {
        "qwen3-30b": {
          "limit": { "context": ${CONTEXT_LIMIT}, "output": ${OUTPUT_LIMIT} }
        }
      }
    }$( [[ -n "$LLAMA_SVC_DNS" ]] && cat <<LLAMA_BLOCK
,
    "vllm-llama": {
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "baseURL": "http://${LLAMA_SVC_DNS}:8080/v1",
        "name": "vllm-llama"
      },
      "models": {
        "llama-32-3b-instruct": {
          "limit": { "context": 14000, "output": 2000 }
        }
      }
    }
LLAMA_BLOCK
)
  }
}
TINYCODE_EOF'
oc delete pod -n tinycode-dev --all
EOF
