# Changelog

## [0.1.0] — 2026-06-26

Initial public release.

### Added
- TinycodeInstance CRD for declarative tinycode management
- kopf-based Python operator with create/update/delete handlers
- Declarative vLLM configuration with auto-probing of /v1/models
- Cross-namespace vLLM service discovery via spec.discovery.namespaces
- GitOps mode — init container clones git repo via spec.git
- Shared team workspace with ReadWriteMany PVC support
- Cluster-admin mode with kubeconfig mounting
- OpenShift SCC binding (restricted, hostpath, shell tiers)
- Helm chart-based deployment with ConfigMap config delivery
- OLM bundle for OperatorHub installation
- File-Based Catalog for private catalog deployment

### Security
- CRD validation patterns (image registry allowlist, git URL/branch, clusterRole allowlist)
- SSRF prevention in vLLM URL probing
- Helm template value quoting
- SCC RBAC with scoped patch/update permissions
- Kubeconfig exception sanitization
- NetworkPolicy Helm template
- Secret read audit logging
