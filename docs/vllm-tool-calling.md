# Enabling Tool Calling on vLLM Deployments

tinycode sends `tool_choice: "auto"` with every session request because it uses
tools (file read/write, bash, grep, etc.) for all coding tasks. vLLM requires
two additional startup flags to support this. Without them every request returns:

```
"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set
```

## Required vLLM Flags

| Flag | Value | Purpose |
|------|-------|---------|
| `--enable-auto-tool-choice` | *(flag only)* | Unlocks automatic tool/function calling |
| `--tool-call-parser` | `hermes` | Tells vLLM which tool call format the model uses |

The parser value depends on the model family:

| Model family | `--tool-call-parser` value |
|--------------|--------------------------|
| Qwen3, Qwen2.5 | `hermes` |
| Llama 3.x | `llama3_json` |
| Mistral | `mistral` |
| DeepSeek | `deepseek_v2` |
| Generic OpenAI-compatible | `pythonic` |

---

## How to Apply — Plain Kubernetes Deployment

The Qwen3-30B deployment on this cluster is a standard `Deployment` in the
`qwen3` namespace. Patch the container args:

```bash
oc patch deployment qwen3-30b -n qwen3 --type=json -p='[
  {"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--enable-auto-tool-choice"},
  {"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--tool-call-parser"},
  {"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"hermes"}
]'
```

Or edit the Deployment directly:

```bash
oc edit deployment qwen3-30b -n qwen3
```

Find the `args:` section under `containers:` and add the two flags:

```yaml
containers:
  - args:
    - --port=8080
    - --model=Qwen/Qwen3-30B-A3B-GPTQ-Int4
    - --served-model-name=qwen3-30b
    - --quantization=gptq_marlin
    - --max-model-len=8192
    - --gpu-memory-utilization=0.90
    - --trust-remote-code
    - --enable-auto-tool-choice          # ADD THIS
    - --tool-call-parser                 # ADD THIS
    - hermes                             # ADD THIS
```

The deployment will roll out a new pod automatically. The readiness probe
(`/health`) will pass once vLLM has loaded the model (~2 minutes).

---

## How to Apply — RHOAI KServe InferenceService

If the model is deployed as a KServe `InferenceService` (RHOAI model serving UI),
add the args to the `predictor.model.args` field:

```yaml
apiVersion: serving.kserve.io/v1beta1
kind: InferenceService
metadata:
  name: qwen3-30b
  namespace: qwen3
spec:
  predictor:
    model:
      args:
        - --enable-auto-tool-choice
        - --tool-call-parser
        - hermes
```

Apply with:

```bash
oc patch inferenceservice qwen3-30b -n qwen3 --type=merge -p '{
  "spec": {
    "predictor": {
      "model": {
        "args": ["--enable-auto-tool-choice", "--tool-call-parser", "hermes"]
      }
    }
  }
}'
```

---

## Verify Tool Calling Works

After the pod restarts, test from inside the tinycode pod:

```bash
POD=$(oc get pods -n tinycode-dev --no-headers | awk 'NR==1{print $1}')

oc exec -n tinycode-dev "$POD" -- curl -s \
  http://172.30.151.43:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3-30b",
    "messages": [{"role":"user","content":"hi"}],
    "tools": [{"type":"function","function":{"name":"test","description":"test","parameters":{"type":"object","properties":{}}}}],
    "tool_choice": "auto",
    "max_tokens": 10
  }' | python3 -m json.tool | grep -E "finish_reason|content|error"
```

A successful response has `"finish_reason": "stop"` or `"tool_calls"`, not an error.

---

## After Enabling Tool Calling in tinycode Config

Once the vLLM server has the flags, remove the `"toolcall": false` workaround
from the tinycode config in the pod:

```bash
POD=$(oc get pods -n tinycode-dev --no-headers | awk 'NR==1{print $1}')

oc exec -n tinycode-dev "$POD" -- sh -c 'cat > /home/tinycode/.config/tinycode/tinycode.json << '"'"'EOF'"'"'
{
  "model": "vllm-qwen3/qwen3-30b",
  "provider": {
    "vllm-qwen3": {
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "baseURL": "http://172.30.151.43:8080/v1",
        "name": "vllm-qwen3"
      },
      "models": {
        "qwen3-30b": {
          "limit": { "context": 6500, "output": 1500 }
        }
      }
    }
  }
}
EOF'

oc delete pod -n tinycode-dev --all
```

---

## Increasing the Context Window

The default `--max-model-len=8192` leaves only ~4-5K tokens for conversation after system
prompt and tool schemas. For a coding assistant this is marginal — a single file read can
consume most of it.

### Why it's limited

The KV (key-value) attention cache grows linearly with sequence length and consumes GPU VRAM.
At 8k tokens the KV cache uses ~4GB in float16 on a 24GB L4, leaving almost nothing free.

### Fix: FP8 KV cache quantization

Modern GPUs (Ada Lovelace / Ampere and newer) support FP8 natively. Quantizing the KV cache
from FP16 → FP8 halves its memory, allowing roughly double the context for the same VRAM:

| `--max-model-len` | KV dtype | KV cache memory | Fits on L4 24GB? |
|------------------|----------|-----------------|-----------------|
| 8192 (current)   | fp16     | ~4 GB           | Yes             |
| 16384            | fp8      | ~4 GB           | Yes             |
| 32768            | fp8      | ~8 GB           | Likely yes      |
| 65536            | fp8      | ~16 GB          | Probably not    |

### Apply the patch

```bash
oc patch deployment qwen3-30b -n qwen3 --type=json -p='[
  {"op":"replace","path":"/spec/template/spec/containers/0/args/4","value":"--max-model-len=32768"},
  {"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kv-cache-dtype"},
  {"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"fp8"},
  {"op":"replace","path":"/spec/template/spec/containers/0/args/5","value":"--gpu-memory-utilization=0.95"}
]'
```

If the pod OOMs during startup (check `oc describe pod -n qwen3`), reduce to 16k:

```bash
oc patch deployment qwen3-30b -n qwen3 --type=json -p='[
  {"op":"replace","path":"/spec/template/spec/containers/0/args/4","value":"--max-model-len=16384"}
]'
```

### After the patch

Update the tinycode context limit to match (80% for conversation, 20% for output):

| `max-model-len` | `limit.context` | `limit.output` |
|-----------------|-----------------|----------------|
| 16384           | 13000           | 2000           |
| 32768           | 26000           | 4000           |

Update the tinycode config in the pod:

```bash
POD=$(oc get pods -n tinycode-dev --no-headers | awk 'NR==1{print $1}')
oc exec -n tinycode-dev "$POD" -- sh -c 'cat > /home/tinycode/.config/tinycode/tinycode.json << '"'"'EOF'"'"'
{
  "model": "vllm-qwen3/qwen3-30b",
  "small_model": "vllm-llama/llama-32-3b-instruct",
  "provider": {
    "vllm-qwen3": {
      "npm": "@ai-sdk/openai-compatible",
      "options": { "baseURL": "http://172.30.151.43:8080/v1", "name": "vllm-qwen3" },
      "models": {
        "qwen3-30b": { "limit": { "context": 26000, "output": 4000 } }
      }
    },
    "vllm-llama": {
      "npm": "@ai-sdk/openai-compatible",
      "options": { "baseURL": "http://llama-32-3b-instruct-predictor.my-first-model.svc.cluster.local:8080/v1", "name": "vllm-llama" },
      "models": {
        "llama-32-3b-instruct": { "limit": { "context": 14000, "output": 2000 } }
      }
    }
  }
}
EOF'
oc delete pod -n tinycode-dev --all
```

Verify available GPU memory supports the higher value before changing.
