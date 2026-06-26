# Open Questions

## Cluster-Admin Agent v1 - 2026-06-22

- [x] **curl in runtime image** — Add `microdnf install -y curl` to ContainerFile runtime stage. 5MB overhead is acceptable. *(Decision: A)*

- [x] **oc binary library compatibility** — Use the **static oc binary** from Red Hat mirrors (no shared library dependencies). No `ldd` validation needed. *(Decision: C)*

- [x] **oc download size and startup impact** — Increase readiness probe `initialDelaySeconds` to **60s**. No configurable timeout field in CRD. *(Decision: A, 60s)*

- [x] **Kubeconfig with multiple contexts** — Document that the Secret should contain a single-context kubeconfig. Operator validates the Secret at reconcile time and sets a `ClusterAdminWarning` status condition if multiple contexts are detected (does not block deployment). *(Decision: B + C)*

- [x] **Kubeconfig token expiry** — All three:
  - **A** Document: kubeconfig must use a long-lived ServiceAccount token, not an `oc login` OAuth token (expires in 24h)
  - **B** Operator detects short-lived tokens (checks token field format in the Secret) and warns in status condition
  - **C** Docs provide the exact helper command to generate the correct long-lived SA token kubeconfig
  *(Decision: A + B + C)*

- [x] **Concurrent cluster-admin sessions (replicas > 1)** — Document the risk of concurrent mutations. Recommend `replicas: 1` for cluster-admin mode. Do NOT enforce it — users decide. *(Decision: A + C)*

- [x] **OpenShift vs vanilla Kubernetes** — Auto-detect at startup: entrypoint runs `oc api-resources | grep -q route.openshift.io` and sets `TINYCODE_CLUSTER_TYPE=openshift` or `kubernetes`. Agent prompt adapts based on this env var — full OpenShift language when detected, generic k8s language otherwise. *(Decision: C)*

## OLM Bundle for OperatorHub - 2026-06-25

- [ ] **Private catalog vs community OperatorHub** — Is this bundle for private/internal catalog only, or intended for community OperatorHub submission? Community submission requires scorecard tests, richer metadata, and passing OperatorHub validators. Assumed private-only for v0.1.0.

- [ ] **SCC installation strategy** — SCCs (tinycode-restricted, tinycode-hostpath, tinycode-shell) are cluster-scoped resources. Should they be pre-installed by cluster-admin (current assumption), or bundled with the operator? OLM has limited support for cluster-scoped resources in non-admin installs.

- [ ] **CSV command vs Dockerfile ENTRYPOINT mismatch** — CSV specifies `command: [python3.11, /app/main.py]` but Dockerfile uses `ENTRYPOINT ["python3.11", "-m", "kopf", "run", ...]`. The CSV command overrides the ENTRYPOINT at runtime. These must be reconciled — which is the correct invocation?

- [ ] **Scorecard tests for v0.1.0** — Should operator-sdk scorecard tests be mandatory before bundle publish, or deferred to v0.2.0? Assumed optional for v0.1.0.

- [ ] **Version upgrade path** — When v0.2.0 ships, should the new CSV include `replaces: tinycode-operator.v0.1.0`? The FBC channel entries need to be designed now to support future upgrade chains. Assumed yes, documented as future work.

- [ ] **Icon for OperatorHub** — CSV has `icon.base64data: ""` (empty). A real or placeholder icon is needed. What icon should represent the operator in OperatorHub?

## Declarative vLLM Config - 2026-06-25

- [ ] **ConfigMap merge with cluster-admin config** — When both `clusterAdmin.enabled` and `spec.vllm` are set, the operator produces a single merged `TINYCODE_CONFIG_CONTENT` JSON containing both `agent` (from cluster-admin) and `provider`+`model` (from vllm) keys. Verify that the existing cluster-admin ConfigMap template is refactored into a unified ConfigMap, not two competing ConfigMaps setting the same env var.

- [ ] **Provider ID collision with local-discovery** — Operator-generated provider IDs (from `spec.vllm[].name`) may collide with local-discovery provider IDs (format `vllm-<service-name>`). The `provider.ts` code at line 737 skips providers that already exist, so operator config wins. But if provider IDs do not match (e.g., operator uses `vllm-qwen3` and discovery uses `vllm-qwen3-30b`), the user may see duplicates. Document that the `name` field should match what local-discovery would generate, or accept that both sources may appear.

- [ ] **Reconcile-time probing vs runtime discovery staleness** — The operator probes `/v1/models` at reconcile time (point-in-time snapshot) while `local-discovery.ts` polls every 30 seconds. If a vLLM service restarts and its `max_model_len` changes, the operator config retains the old limits until the next CR update triggers reconcile. This is acceptable because user-provided limits are the authoritative source, and auto-detected limits are a convenience. Document this behavior.

- [ ] **vLLM returns multiple models from a single endpoint** — A single vLLM service can serve multiple model aliases. If the user does not specify a `models` dict, the operator auto-detects all models from `/v1/models` and generates entries for each. If the user specifies a partial `models` dict, only those models get user-provided limits; remaining models get auto-detected limits. Confirm this behavior is desirable.

## Cross-Namespace vLLM Discovery - 2026-06-25

- [ ] **Cluster-wide `*` discovery security posture** — The `*` sentinel grants `list services` across all namespaces, which reveals service names and ports in every namespace. This is read-only and limited to service metadata, but may expose information about internal services in unrelated namespaces. Document the security implications and recommend named namespaces over `*` for production clusters.

- [ ] **RHOAI InferenceService creates multiple Service objects** — KServe creates `predictor`, `transformer`, and `explainer` Services per model. Only the `predictor` has the actual model endpoint. The existing `local-discovery.ts` probes all candidates but only registers those that respond to `/v1/models`, which naturally filters out non-predictor services. No explicit filtering needed, but document this behavior to avoid confusion when users see probe failures in logs.

- [ ] **Namespace deletion race condition** — If a namespace in `spec.discovery.namespaces` is deleted, `local-discovery.ts` will get 403/404 when listing services in that namespace. This should be handled gracefully (log warning, skip namespace, continue). Verify that the existing error handling in `local-discovery.ts` covers this case.

- [ ] **local-discovery.ts cross-namespace URL format** — When discovering a service in namespace `qwen3` from a pod in namespace `tinycode-dev`, the service URL must use the fully-qualified `<svc-name>.<namespace>.svc.cluster.local:<port>` format. The existing code may construct URLs using only `<cluster-ip>:<port>` (line 385). If so, this works cross-namespace (ClusterIPs are cluster-scoped), but DNS names are more resilient. Decide which format to use.

## GitOps Mode (#6) - 2026-06-25

- [ ] **Init container image** — Plan assumes tinycode container image includes git (entrypoint.sh uses it). Verify with `docker run --rm quay.io/bjohns/tinycode-container:latest which git`. If not present, a dedicated UBI-based git image is needed.
- [ ] **RBAC gap: SCC patching** — `operator/main.py:190` calls `scc_api.patch()` but `config/rbac/scc_role.yaml` only grants `get/list/watch/use`, not `patch`. This is a pre-existing issue that both #6 and #7 depend on. Verify if SCC binding currently works in production. If broken, fix before adding init container SCC requirements.
- [ ] **Git credential rotation** — If `credentialsSecret` is updated (token rotation), existing pods do not pick up new credentials until restart. Consider extending the annotation checksum pattern to trigger rolling restarts on secret change.

## Shared Team Workspace (#7) - 2026-06-25

- [ ] **Ephemeral data PVC tradeoff** — Plan uses `emptyDir` for per-pod data when `replicas > 1`, meaning session history is lost on pod restart. Confirm this is acceptable for v1. Alternative: StatefulSet with `volumeClaimTemplates` (significant scope increase, deferred).
- [ ] **PVC access mode immutability** — Users upgrading existing instances from RWO to RWX cannot change the PVC in-place. The operator warns but does not auto-migrate. Document the manual PVC deletion + recreation process (data loss on projects PVC unless backed up).
- [ ] **Scale-down behavior** — When scaling from replicas 3 to 1, users connected to terminated pods lose their sessions. Document graceful scale-down expectations or add pod disruption budget.
- [ ] **Concurrent git operations on shared PVC** — When GitOps mode (#6) and shared workspace (#7) are combined, concurrent `git pull` operations on the same RWX volume could corrupt the git index (`.git/index.lock` contention). May need a leader-election pattern or file lock for git operations. Test under load.
