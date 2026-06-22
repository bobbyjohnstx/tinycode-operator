# RHOAI Cluster Setup for tinycode

This document contains the exact commands to run when setting up an RHOAI cluster
for use with the tinycode operator. These should be part of the cluster provisioning
playbook — run them once when the cluster is created, before deploying the tinycode
operator.

The tinycode operator will **detect and warn** at install time and at reconcile time
if these requirements are not met. See the "What the Operator Checks" section at the
bottom for details.

---

## 1. Deploy vLLM Model(s)

Each vLLM deployment needs three groups of flags:
- **Tool calling** — required for tinycode's file/shell tools to work
- **Context window** — increase from the default 8k to enable real coding sessions
- **KV cache quantization** — enables larger context on memory-constrained GPUs

### Qwen3-30B (recommended primary model)

```bash
cat <<'EOF' | oc apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: qwen3-30b
  namespace: qwen3
spec:
  replicas: 1
  selector:
    matchLabels:
      app: qwen3-30b
  template:
    metadata:
      labels:
        app: qwen3-30b
        tinycode.dev/discover: vllm          # enables tinycode auto-discovery
    spec:
      tolerations:
        - key: nvidia.com/gpu
          operator: Exists
          effect: NoSchedule
      containers:
        - name: vllm
          image: registry.redhat.io/rhaiis/vllm-cuda-rhel9:3.2.4
          args:
            - --port=8080
            - --model=Qwen/Qwen3-30B-A3B-GPTQ-Int4
            - --served-model-name=qwen3-30b
            - --quantization=gptq_marlin
            - --max-model-len=32768           # 32k context (was 8192)
            - --gpu-memory-utilization=0.95
            - --trust-remote-code
            - --kv-cache-dtype                # halves KV cache memory (L4 supports fp8)
            - fp8
            - --enable-auto-tool-choice       # required for tinycode tool calling
            - --tool-call-parser              # required for tinycode tool calling
            - hermes                          # Qwen3/Qwen2.5 use hermes format
          ports:
            - containerPort: 8080
              name: http
          resources:
            limits:
              nvidia.com/gpu: "1"
            requests:
              cpu: "4"
              memory: 24Gi
              nvidia.com/gpu: "1"
          readinessProbe:
            httpGet:
              path: /health
              port: 8080
            initialDelaySeconds: 120
            periodSeconds: 15
            failureThreshold: 20
---
apiVersion: v1
kind: Service
metadata:
  name: qwen3-30b
  namespace: qwen3
  labels:
    app: qwen3-30b
    tinycode.dev/discover: vllm              # enables tinycode auto-discovery
spec:
  selector:
    app: qwen3-30b
  ports:
    - name: http
      port: 8080
      targetPort: 8080
EOF
```

**GPU note:** If the pod OOMs during startup, reduce `--max-model-len` incrementally:
```
32768 → 16384 → 8192
```
Check with: `oc describe pod -n qwen3 -l app=qwen3-30b | grep -A5 OOM`

### Llama-3.2-3B (small model for compaction)

A small fast model used by tinycode for session compaction. Set in `small_model`
config. Without this, compaction falls back to the primary model which causes loops
on models with small context windows.

```bash
cat <<'EOF' | oc apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: llama-32-3b
  namespace: llama
spec:
  replicas: 1
  selector:
    matchLabels:
      app: llama-32-3b
  template:
    metadata:
      labels:
        app: llama-32-3b
        tinycode.dev/discover: vllm
    spec:
      tolerations:
        - key: nvidia.com/gpu
          operator: Exists
          effect: NoSchedule
      containers:
        - name: vllm
          image: registry.redhat.io/rhaiis/vllm-cuda-rhel9:3.2.4
          args:
            - --port=8080
            - --model=meta-llama/Llama-3.2-3B-Instruct
            - --served-model-name=llama-32-3b-instruct
            - --max-model-len=20000
            - --gpu-memory-utilization=0.90
            - --trust-remote-code
            - --enable-auto-tool-choice
            - --tool-call-parser
            - llama3_json                     # Llama 3.x uses llama3_json format
          resources:
            limits:
              nvidia.com/gpu: "1"
            requests:
              cpu: "2"
              memory: 8Gi
              nvidia.com/gpu: "1"
          readinessProbe:
            httpGet:
              path: /health
              port: 8080
            initialDelaySeconds: 60
            periodSeconds: 10
---
apiVersion: v1
kind: Service
metadata:
  name: llama-32-3b
  namespace: llama
  labels:
    tinycode.dev/discover: vllm
spec:
  selector:
    app: llama-32-3b
  ports:
    - name: http
      port: 8080
      targetPort: 8080
EOF
```

---

## 2. Verify Before Installing the Operator

Run these checks after the pods are Ready:

```bash
# Check tool calling works on Qwen3
QWEN3_IP=$(oc get svc qwen3-30b -n qwen3 -o jsonpath='{.spec.clusterIP}')
oc run vllm-check --image=registry.access.redhat.com/ubi9/ubi-minimal:latest \
  --restart=Never --rm -it --command -- \
  curl -sf -X POST http://${QWEN3_IP}:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3-30b","messages":[{"role":"user","content":"hi"}],
       "tools":[{"type":"function","function":{"name":"t","description":"t",
       "parameters":{"type":"object","properties":{}}}}],
       "tool_choice":"auto","max_tokens":5}' | grep -v "enable-auto-tool-choice"
echo "Tool calling OK if no error above"

# Check context window
oc exec -n qwen3 $(oc get pods -n qwen3 -l app=qwen3-30b -o name | head -1) \
  -- curl -s http://localhost:8080/v1/models | python3 -c \
  "import sys,json; m=json.load(sys.stdin)['data'][0]; print(f'Model: {m[\"id\"]}  Context: {m[\"max_model_len\"]} tokens')"
```

---

## 3. Required vLLM Flags Summary

| Flag | Value | Why |
|------|-------|-----|
| `--enable-auto-tool-choice` | *(flag only)* | tinycode sends tool schemas with every request |
| `--tool-call-parser` | `hermes` (Qwen3) / `llama3_json` (Llama) | Parses tool call JSON from model output |
| `--kv-cache-dtype` | `fp8` | Halves KV cache memory, enables 4× larger context on same GPU |
| `--max-model-len` | `32768` | Gives ~26k tokens for conversation after system prompt + tools |
| `--gpu-memory-utilization` | `0.95` | Maximises available KV cache (safe on dedicated GPU nodes) |

The `tinycode.dev/discover: vllm` label on the Service is optional but enables
Kubernetes-native auto-discovery from the tinycode pod without manual endpoint config.

---

## 4. What the Operator Checks

The tinycode operator validates vLLM configuration at two points:

### At `hack/install.sh`

Scans all Deployments in the cluster for vLLM containers and warns if
`--enable-auto-tool-choice` or `--tool-call-parser` are missing:

```
[WARN]  ┌──────────────────────────────────────────────────────────────────────┐
[WARN]  │  ACTION REQUIRED: vLLM tool calling not enabled                      │
[WARN]  │  Fix: add --enable-auto-tool-choice and --tool-call-parser to args   │
[WARN]  │  Full guide: docs/vllm-tool-calling.md                               │
[WARN]  └──────────────────────────────────────────────────────────────────────┘
```

### At TinycodeInstance reconcile time

After each Helm deploy, the operator probes vLLM services in the instance namespace
and sets a `ToolCallingWarning` condition on the CR:

```bash
oc describe tinycodeinstance my-tinycode -n tinycode-dev

# Conditions:
#   Type                  Status  Reason
#   ----                  ------  ------
#   Ready                 True    ReconcileSuccess
#   ToolCallingWarning    True    VllmToolCallingNotConfigured
#                                 vLLM service qwen3-30b does not support tool
#                                 calling. Add --enable-auto-tool-choice and
#                                 --tool-call-parser. See docs/vllm-tool-calling.md
```

### Context window check (planned — operator issue #1)

Currently the operator does **not** check context window size. This will be added
when the declarative vLLM config in the `TinycodeInstance` spec is implemented
(tinycode-operator issue #1). At that point, the operator will warn when
`max_model_len < 16384` since anything smaller causes compaction loops for typical
coding sessions.

**Until then:** ensure `--max-model-len` is set correctly before deploying
tinycode instances, using the verification command in step 2 above.

---

## 5. Recommended tinycode Configuration

After the operator deploys a TinycodeInstance, update the model config to match
the deployed models. Until operator issue #1 is implemented, this is a manual step:

```bash
POD=$(oc get pods -n tinycode-dev --no-headers | awk 'NR==1{print $1}')

oc exec -n tinycode-dev "$POD" -- sh -c 'cat > /home/tinycode/.config/tinycode/tinycode.json << '"'"'EOF'"'"'
{
  "model": "vllm-qwen3/qwen3-30b",
  "small_model": "vllm-llama/llama-32-3b-instruct",
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
    },
    "vllm-llama": {
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "baseURL": "http://llama-32-3b.llama.svc.cluster.local:8080/v1",
        "name": "vllm-llama"
      },
      "models": {
        "llama-32-3b-instruct": {
          "limit": { "context": 14000, "output": 2000 }
        }
      }
    }
  }
}
EOF'

oc delete pod -n tinycode-dev --all
```

This step will be automated by tinycode-operator issue #1 (declarative vLLM config
in the TinycodeInstance spec).
