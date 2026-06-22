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
