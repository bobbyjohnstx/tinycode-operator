# Changelog

## [Unreleased]

### Fixed
- hostPath SCC selection bug (checked `enabled` instead of `path`)
- readOnly hostPath applied in Helm deployment template
- SCC runAsUser enforced as MustRunAs UID 1001 (restricted + hostpath SCCs)
- observedGeneration set in CR status updates
- Cluster-wide secret RBAC reduced to get-only (removed list/watch)
- Helm binary download checksum verification
- Spec hash skip for no-op Helm upgrades
- DynamicClient reuse (cached instead of per-call)
- CSV liveness flag matches Dockerfile ENTRYPOINT

### Added
- 44 unit tests (pytest) covering pure functions and validation logic
- Dependabot configuration for GitHub Actions and pip

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
