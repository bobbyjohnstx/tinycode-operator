# OLM Bundle and OperatorHub Deployment

This guide covers building, testing, and deploying the tinycode-operator OLM bundle for OpenShift OperatorHub and community catalog distribution.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Bundle Structure](#bundle-structure)
- [Build Workflow](#build-workflow)
- [Local Testing](#local-testing)
- [OpenShift Verification](#openshift-verification)
- [Private Catalog Deployment](#private-catalog-deployment)
- [Air-Gapped Deployment](#air-gapped-deployment)
- [Cleanup](#cleanup)
- [Troubleshooting](#troubleshooting)

## Prerequisites

Install the following tools:

- **operator-sdk** (v1.34.0+)
  ```bash
  curl -LO https://github.com/operator-framework/operator-sdk/releases/latest/download/operator-sdk_linux_amd64
  install operator-sdk_linux_amd64 /usr/local/bin/operator-sdk
  ```

- **opm** (Operator Package Manager)
  ```bash
  curl -LO https://github.com/operator-framework/operator-registry/releases/latest/download/linux-amd64-opm
  install linux-amd64-opm /usr/local/bin/opm
  ```

- **podman** (or docker)
  ```bash
  # On Fedora/RHEL
  sudo dnf install podman

  # On macOS
  brew install podman
  podman machine init
  podman machine start
  ```

- **oc CLI** (OpenShift CLI)
  ```bash
  curl -LO https://mirror.openshift.com/pub/openshift-v4/clients/ocp/stable/openshift-client-linux.tar.gz
  tar -xzf openshift-client-linux.tar.gz
  install oc /usr/local/bin/oc
  ```

## Bundle Structure

The OLM bundle follows the standard Operator Framework layout:

```
bundle/
├── manifests/
│   ├── tinycode-operator.clusterserviceversion.yaml  # CSV
│   └── tinycode.dev_tinycodeinstances.yaml           # CRD
└── metadata/
    └── annotations.yaml                               # Bundle metadata
```

The bundle is packaged as a container image using `bundle.Dockerfile`.

## Build Workflow

### 1. Validate the Bundle

```bash
make bundle-validate
```

This runs `operator-sdk bundle validate` with the `operatorframework` suite to catch schema errors, missing fields, and OLM compatibility issues.

**Expected output:**
```
INFO[0000] All validation tests have completed successfully
```

### 2. Build the Bundle Image

```bash
# Use default version (0.1.0)
make bundle-build

# Or specify a different version
VERSION=0.2.0 make bundle-build
```

This creates a bundle image at `quay.io/tinycode/operator-bundle:v0.1.0`.

### 3. Push the Bundle Image

```bash
# Login to your registry first
podman login quay.io

# Push the bundle
make bundle-push
```

### 4. Build the Catalog Image

The catalog is a File-Based Catalog (FBC) that references the bundle:

```bash
make catalog-build
```

Or use the modern FBC approach (recommended):

```bash
podman build -f catalog.Dockerfile -t quay.io/tinycode/operator-catalog:v0.1.0 .
```

### 5. Push the Catalog Image

```bash
make catalog-push
```

## Local Testing

Test the bundle locally without installing it cluster-wide using `operator-sdk run bundle`.

### 1. Run Bundle Locally

```bash
# Ensure you're logged into an OpenShift cluster
oc whoami

# Run the bundle (installs to current namespace)
make test-bundle
```

This:
- Creates an OLM `Subscription` and `InstallPlan`
- Deploys the operator to the current namespace
- Does NOT install cluster-scoped resources (SCCs, CatalogSource)

### 2. Create a TinycodeInstance

```bash
oc apply -f config/samples/tinycode_v1alpha1_basic.yaml
```

### 3. Verify Deployment

```bash
oc get tinycodeinstance
oc get pods
oc get routes
```

### 4. Cleanup Local Test

```bash
operator-sdk cleanup tinycode-operator
```

## OpenShift Verification

Test the full catalog installation flow on OpenShift.

### 1. Deploy the CatalogSource

```bash
oc apply -f config/catalog/catalogsource.yaml
```

### 2. Verify CatalogSource is Ready

```bash
oc get catalogsource -n openshift-marketplace
oc get pods -n openshift-marketplace | grep tinycode
```

Wait for the catalog pod to reach `Running` state.

### 3. Install via OperatorHub UI

1. Navigate to **Operators → OperatorHub** in the OpenShift Console
2. Search for "Tinycode Operator"
3. Click **Install**
4. Select installation namespace (e.g., `tinycode-system`)
5. Click **Install** again

### 4. Install via CLI

Create a subscription manually:

```yaml
apiVersion: operators.coreos.com/v1alpha1
kind: Subscription
metadata:
  name: tinycode-operator
  namespace: tinycode-system
spec:
  channel: alpha
  name: tinycode-operator
  source: tinycode-catalog
  sourceNamespace: openshift-marketplace
```

Apply it:

```bash
oc create namespace tinycode-system
oc apply -f subscription.yaml
```

### 5. Verify Installation

```bash
# Check CSV
oc get csv -n tinycode-system

# Check operator pod
oc get pods -n tinycode-system

# Check operator logs
oc logs -n tinycode-system deployment/tinycode-operator-manager -f
```

## Private Catalog Deployment

For on-premises or air-gapped clusters, use a private catalog.

### 1. Build and Push to Private Registry

```bash
# Set your private registry
export IMAGE_REGISTRY=registry.example.com
export IMAGE_ORG=operators

# Build operator, bundle, and catalog
make build push
make bundle-build bundle-push
make catalog-build catalog-push
```

### 2. Update CatalogSource

Edit `config/catalog/catalogsource.yaml`:

```yaml
spec:
  image: registry.example.com/operators/operator-catalog:v0.1.0
```

### 3. Deploy CatalogSource

```bash
oc apply -f config/catalog/catalogsource.yaml
```

## Air-Gapped Deployment

For disconnected OpenShift clusters, mirror images to the internal registry.

### 1. Mirror Operator Image

```bash
# Tag for internal registry
podman tag quay.io/tinycode/operator:latest \
  image-registry.openshift-image-registry.svc:5000/tinycode-system/operator:v0.1.0

# Push to OpenShift internal registry
podman push image-registry.openshift-image-registry.svc:5000/tinycode-system/operator:v0.1.0 \
  --tls-verify=false
```

### 2. Mirror Bundle Image

```bash
podman tag quay.io/tinycode/operator-bundle:v0.1.0 \
  image-registry.openshift-image-registry.svc:5000/tinycode-system/operator-bundle:v0.1.0

podman push image-registry.openshift-image-registry.svc:5000/tinycode-system/operator-bundle:v0.1.0 \
  --tls-verify=false
```

### 3. Mirror Catalog Image

```bash
podman tag quay.io/tinycode/operator-catalog:v0.1.0 \
  image-registry.openshift-image-registry.svc:5000/openshift-marketplace/operator-catalog:v0.1.0

podman push image-registry.openshift-image-registry.svc:5000/openshift-marketplace/operator-catalog:v0.1.0 \
  --tls-verify=false
```

### 4. Update CatalogSource for Internal Registry

```yaml
apiVersion: operators.coreos.com/v1alpha1
kind: CatalogSource
metadata:
  name: tinycode-catalog
  namespace: openshift-marketplace
spec:
  sourceType: grpc
  image: image-registry.openshift-image-registry.svc:5000/openshift-marketplace/operator-catalog:v0.1.0
  displayName: Tinycode Operator Catalog (Internal)
  publisher: tinycode community
```

### 5. Create ImageContentSourcePolicy (Optional)

For automatic mirroring of the tinycode container image:

```yaml
apiVersion: operator.openshift.io/v1alpha1
kind: ImageContentSourcePolicy
metadata:
  name: tinycode-mirror
spec:
  repositoryDigestMirrors:
    - mirrors:
        - image-registry.openshift-image-registry.svc:5000/tinycode-system
      source: quay.io/bjohns/tinycode-container
```

## Cleanup

### Remove CatalogSource

```bash
oc delete catalogsource tinycode-catalog -n openshift-marketplace
```

### Uninstall Operator via Subscription

```bash
oc delete subscription tinycode-operator -n tinycode-system
oc delete csv -n tinycode-system -l operators.coreos.com/tinycode-operator.tinycode-system
```

### Remove Operator Namespace

```bash
oc delete namespace tinycode-system
```

### Cleanup operator-sdk Local Test

```bash
operator-sdk cleanup tinycode-operator
```

## Troubleshooting

### Bundle Validation Fails

**Problem:** `operator-sdk bundle validate` reports schema errors.

**Solution:**
1. Check the CSV at `bundle/manifests/tinycode-operator.clusterserviceversion.yaml`
2. Ensure `minKubeVersion`, `icon`, `containerImage`, and `installModes` are set
3. Verify CRD is present in `bundle/manifests/`
4. Run validation with verbose output:
   ```bash
   operator-sdk bundle validate ./bundle --select-optional suite=operatorframework -v
   ```

### CatalogSource Pod CrashLoopBackOff

**Problem:** Catalog pod fails to start.

**Solution:**
1. Check pod logs:
   ```bash
   oc logs -n openshift-marketplace $(oc get pods -n openshift-marketplace -l olm.catalogSource=tinycode-catalog -o name)
   ```
2. Verify the catalog image is accessible:
   ```bash
   podman pull quay.io/tinycode/operator-catalog:v0.1.0
   ```
3. Check FBC catalog syntax:
   ```bash
   opm validate catalog/
   ```

### Operator Subscription Stuck in "UpgradePending"

**Problem:** `oc get subscription` shows `UpgradePending` but operator doesn't deploy.

**Solution:**
1. Check InstallPlan:
   ```bash
   oc get installplan -n tinycode-system
   ```
2. Approve manual InstallPlans:
   ```bash
   oc patch installplan <name> -n tinycode-system --type merge -p '{"spec":{"approved":true}}'
   ```
3. Check for missing RBAC or SCCs:
   ```bash
   oc get clusterserviceversion -n tinycode-system -o yaml
   ```

### Operator Pods Fail with "Unable to use SCC"

**Problem:** Operator pod logs show `unable to validate against any security context constraint`.

**Solution:**
1. Ensure SCCs are created:
   ```bash
   oc get scc tinycode-restricted tinycode-hostpath tinycode-shell
   ```
2. Install SCCs manually if missing:
   ```bash
   oc apply -f config/scc/
   ```
3. Grant SCC to the operator ServiceAccount:
   ```bash
   oc adm policy add-scc-to-user tinycode-restricted -z tinycode-operator-manager -n tinycode-system
   ```

### Bundle Image Pull Fails in Air-Gapped Cluster

**Problem:** OLM cannot pull bundle image from external registry.

**Solution:**
1. Verify image was mirrored to internal registry:
   ```bash
   oc get imagestreams -n tinycode-system
   ```
2. Check pull secret for internal registry:
   ```bash
   oc get secret -n openshift-marketplace | grep pull
   ```
3. Create pull secret if missing:
   ```bash
   oc create secret docker-registry internal-registry-pull \
     --docker-server=image-registry.openshift-image-registry.svc:5000 \
     --docker-username=serviceaccount \
     --docker-password=$(oc whoami -t) \
     -n openshift-marketplace
   ```

### "No package manifests" Error

**Problem:** OperatorHub UI shows "No operators found".

**Solution:**
1. Verify CatalogSource is `READY`:
   ```bash
   oc get catalogsource -n openshift-marketplace
   ```
2. Check packagemanifests:
   ```bash
   oc get packagemanifests | grep tinycode
   ```
3. Restart catalog pod:
   ```bash
   oc delete pod -n openshift-marketplace -l olm.catalogSource=tinycode-catalog
   ```
4. Wait 2-5 minutes for packagemanifest to propagate

## Next Steps

- **Community Catalog Submission:** Submit the bundle to [OperatorHub.io](https://operatorhub.io/contribute)
- **Red Hat Certified Operator:** Apply for Red Hat Operator Certification
- **OpenShift Ecosystem:** List on the Red Hat Ecosystem Catalog
- **Versioning:** Create v0.2.0 bundle for upgrades and update the catalog
- **Testing:** Add CI/CD pipeline for bundle validation and scorecard tests
