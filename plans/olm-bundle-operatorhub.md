# OLM Bundle for OperatorHub / Cluster Catalog

## Context

The tinycode-operator has a ClusterServiceVersion (CSV) at `bundle/manifests/` but is not
published to any OLM catalog. The operator is currently deployed manually via `hack/install.sh`.
Publishing to OperatorHub or a private catalog enables installation via the OpenShift web console
and standard OLM lifecycle management (upgrades, rollbacks, dependency resolution).

Issue: Gitea `bjohns/tinycode-operator#4`

## Work Objectives

- Complete the OLM bundle directory structure so it passes `operator-sdk bundle validate`
- Build and push a bundle image to `quay.io/tinycode/operator-bundle`
- Create a File-Based Catalog (FBC) and catalog image for private catalog deployment
- Add Makefile targets for the full bundle/catalog lifecycle
- Document testing workflow and air-gapped deployment
- Verify end-to-end installation on an OpenShift cluster via OperatorHub UI

## Assumptions

- Bundle targets a **private catalog** for v0.1.0 (not community OperatorHub submission)
- Scorecard tests are **optional** for v0.1.0 (noted as future work)
- SCCs are **pre-installed by cluster-admin** (not bundled — OLM does not manage cluster-scoped SCC resources well for non-admin installs)
- Single channel: `alpha` (matching CSV maturity) with `stable` added when maturity graduates
- Air-gapped docs are informational (image mirroring instructions), not automated tooling
- Container registry: `quay.io/tinycode` for operator, bundle, and catalog images

## Guardrails

**Must Have:**
- Bundle passes `operator-sdk bundle validate` with zero errors
- CRD is present in `bundle/manifests/` (not just in `config/crd/`)
- Bundle image is `FROM scratch` (metadata-only, not a runnable container)
- All LABEL directives in bundle.Dockerfile match annotations.yaml exactly
- Makefile targets have help text and fail-fast on missing tools (opm, operator-sdk)
- CSV `containerImage` annotation uses versioned tag (`:v0.1.0`), not `:latest`

**Must NOT Have:**
- Operator binary or Python code in the bundle image (bundle != operator image)
- Hardcoded registry paths without Makefile variable overrides
- Breaking changes to existing `make build` / `make push` / `make install` targets
- SCC manifests inside the bundle (pre-installed separately by cluster-admin)

## Task Flow

### 1. Complete bundle directory structure and fix CSV

**What:** Create `bundle/metadata/annotations.yaml`, copy the CRD into `bundle/manifests/`,
and fix known CSV issues (empty icon, `:latest` image tag, namespace placeholder).

**Details:**
- Create `bundle/metadata/annotations.yaml` with required OLM annotations:
  - `operators.operatorframework.io.bundle.mediatype.v1: registry+v1`
  - `operators.operatorframework.io.bundle.manifests.v1: manifests/`
  - `operators.operatorframework.io.bundle.metadata.v1: metadata/`
  - `operators.operatorframework.io.bundle.package.v1: tinycode-operator`
  - `operators.operatorframework.io.bundle.channels.v1: alpha`
  - `operators.operatorframework.io.bundle.channel.default.v1: alpha`
- Copy `config/crd/tinycode.dev_tinycodeinstances.yaml` into `bundle/manifests/`
- Update CSV `metadata.annotations.containerImage` from `:latest` to `:v0.1.0`
- Add a placeholder icon (minimum 64x64 PNG, base64-encoded) or a real project icon
- Verify CSV `command` field matches Dockerfile ENTRYPOINT behavior
  - CSV has `command: [python3.11, /app/main.py]`
  - Dockerfile has `ENTRYPOINT ["python3.11", "-m", "kopf", "run", ...]`
  - These must be reconciled (CSV command overrides ENTRYPOINT at runtime)
- Remove `namespace: placeholder` from CSV metadata (OLM sets the namespace at install time)

**Acceptance:** `bundle/` directory contains `manifests/tinycode-operator.clusterserviceversion.yaml`,
`manifests/tinycode.dev_tinycodeinstances.yaml`, and `metadata/annotations.yaml`. The CSV has a
non-empty icon, versioned image tag, and no hardcoded namespace.

---

### 2. Create bundle.Dockerfile

**What:** Create a `bundle.Dockerfile` at the repo root that builds the OLM bundle image.

**Details:**
- Base: `FROM scratch` (OLM bundle images are metadata-only, non-runnable)
- Add 6 required LABEL directives matching `bundle/metadata/annotations.yaml` exactly
- `ADD bundle/manifests /manifests/`
- `ADD bundle/metadata /metadata/`
- Parameterize channel labels via build args if needed for future multi-channel support

**Acceptance:** `podman build -f bundle.Dockerfile -t quay.io/tinycode/operator-bundle:v0.1.0 .`
succeeds and the resulting image is under 1MB (metadata only).

---

### 3. Add Makefile targets for bundle and catalog lifecycle

**What:** Add `bundle-build`, `bundle-push`, `bundle-validate`, `catalog-build`, `catalog-push`
targets to the Makefile.

**Details:**
- New variables:
  - `BUNDLE_IMAGE ?= $(IMAGE_REGISTRY)/$(IMAGE_ORG)/operator-bundle:v$(VERSION)`
  - `CATALOG_IMAGE ?= $(IMAGE_REGISTRY)/$(IMAGE_ORG)/operator-catalog:v$(VERSION)`
  - `VERSION ?= 0.1.0`
- New targets:
  - `bundle-validate` — runs `operator-sdk bundle validate ./bundle --select-optional suite=operatorframework`; checks that `opm` and `operator-sdk` are in PATH
  - `bundle-build` — depends on `bundle-validate`; runs `podman build -f bundle.Dockerfile`
  - `bundle-push` — pushes bundle image to registry
  - `catalog-build` — builds catalog image from FBC directory (see step 4)
  - `catalog-push` — pushes catalog image to registry
  - `test-bundle` — runs `operator-sdk run bundle $(BUNDLE_IMAGE)` against connected cluster
- Add all new targets to `.PHONY` and `help` output
- Existing targets (`build`, `push`, `install`, `uninstall`) remain unchanged

**Acceptance:** `make help` lists all new targets with descriptions. `make bundle-validate`
passes with exit code 0. `make bundle-build` produces a tagged image. `make bundle-push`
pushes to the configured registry.

---

### 4. Create File-Based Catalog (FBC) and CatalogSource manifest

**What:** Create the FBC directory structure and a CatalogSource manifest for deploying
the private catalog on an OpenShift cluster.

**Details:**
- Create `catalog/` directory at repo root
- Create `catalog/tinycode-operator/catalog.yaml` with three FBC schemas:
  - `olm.package` — package metadata (name, defaultChannel, description, icon)
  - `olm.channel` — channel definition (`alpha` channel with entry for v0.1.0)
  - `olm.bundle` — bundle reference (image, properties: olm.package + olm.gvk)
- Create `catalog.Dockerfile` (generated via `opm generate dockerfile catalog` or manually):
  - Base: `quay.io/operator-framework/opm:latest` (or pinned version)
  - Copy catalog data; expose gRPC serving
- Create `config/catalog/catalogsource.yaml`:
  ```yaml
  apiVersion: operators.coreos.com/v1alpha1
  kind: CatalogSource
  metadata:
    name: tinycode-catalog
    namespace: openshift-marketplace
  spec:
    sourceType: grpc
    image: quay.io/tinycode/operator-catalog:v0.1.0
    displayName: Tinycode Operator Catalog
    publisher: tinycode community
    updateStrategy:
      registryPoll:
        interval: 30m
  ```
- Validate FBC with `opm validate catalog/`

**Acceptance:** `opm validate catalog/` passes. `make catalog-build` produces a catalog image.
Applying `catalogsource.yaml` on a cluster makes the tinycode-operator visible in OperatorHub
within 60 seconds.

---

### 5. Document testing and verification workflow

**What:** Add documentation covering how to test the bundle locally, verify on OpenShift,
and deploy in air-gapped environments.

**Details:**
- Add `docs/olm-bundle.md` covering:
  1. **Prerequisites** — required tools (operator-sdk, opm, podman, oc), versions, install instructions
  2. **Build workflow** — step-by-step `make bundle-validate && make bundle-build && make bundle-push`
  3. **Local testing** — `operator-sdk run bundle` against a connected cluster
     - How to use `--skip-tls-verify` for local registries
     - How to pass `--pull-secret-name` for private registries
  4. **OpenShift verification** — apply CatalogSource, verify in OperatorHub UI,
     create Subscription, verify CSV reaches `Succeeded` phase, create sample
     TinycodeInstance, verify operator reconciles successfully
  5. **Air-gapped deployment** — instructions for mirroring images:
     - Mirror `quay.io/tinycode/operator:v0.1.0` to private registry
     - Mirror `quay.io/tinycode/operator-bundle:v0.1.0` to private registry
     - Mirror `quay.io/tinycode/operator-catalog:v0.1.0` to private registry
     - Update CatalogSource to point to private registry
     - Use `oc adm catalog mirror` or `skopeo copy` for image mirroring
  6. **Cleanup** — `operator-sdk cleanup tinycode-operator` or manual resource removal
  7. **Troubleshooting** — common issues (CatalogSource not ready, CSV pending,
     image pull errors, RBAC failures)
- Update README.md with a section pointing to the OLM docs

**Acceptance:** A developer can follow `docs/olm-bundle.md` end-to-end to build, push, test,
and install the operator via OLM without additional guidance. Air-gapped section covers
image mirroring for all three images (operator, bundle, catalog).

---

### 6. End-to-end verification on OpenShift cluster

**What:** Manually verify the complete OLM installation flow on a real OpenShift cluster.

**Details:**
- Build and push all three images (operator, bundle, catalog)
- Apply CatalogSource to cluster
- Verify operator appears in OperatorHub web console
- Install operator via OperatorHub UI (or via Subscription YAML)
- Verify:
  - CSV reaches `Succeeded` phase
  - Operator pod is running with correct RBAC
  - Create a sample TinycodeInstance CR
  - Operator reconciles and creates expected resources (Deployment, Service, Route, PVCs)
  - TinycodeInstance status shows `url` field populated
  - Access the tinycode web UI via the Route URL
- Clean up with `operator-sdk cleanup` or manual deletion
- Document any issues found and fix before merging

**Acceptance:** Operator installs via OperatorHub UI, reconciles a TinycodeInstance CR,
and the tinycode web UI is accessible via the created Route. No RBAC errors in operator logs.
No dangling resources after cleanup.

## Success Criteria

- `operator-sdk bundle validate ./bundle` passes with zero errors
- Bundle image builds and pushes to quay.io/tinycode/operator-bundle:v0.1.0
- Catalog image builds and pushes to quay.io/tinycode/operator-catalog:v0.1.0
- `make help` shows all bundle/catalog targets
- Operator is installable via OperatorHub web console on OpenShift
- Sample TinycodeInstance CR reconciles successfully after OLM-based install
- Air-gapped deployment instructions are documented

## Future Work (out of scope for v0.1.0)

- Operator SDK scorecard tests (`bundle/tests/scorecard/`)
- Community OperatorHub submission (requires passing all OperatorHub validators)
- `replaces:` chain for version upgrades (needed when v0.2.0 ships)
- `stable` channel (when maturity graduates from alpha)
- CI/CD pipeline for automated bundle builds and catalog updates
- Automated SCC installation via bundle (if OLM adds better cluster-scoped resource support)
