IMAGE_REGISTRY ?= quay.io
IMAGE_ORG      ?= tinycode
IMAGE_TAG      ?= latest
VERSION        ?= 0.1.0
OPERATOR_IMAGE ?= $(IMAGE_REGISTRY)/$(IMAGE_ORG)/operator:$(IMAGE_TAG)
BUNDLE_IMAGE   ?= $(IMAGE_REGISTRY)/$(IMAGE_ORG)/operator-bundle:v$(VERSION)
CATALOG_IMAGE  ?= $(IMAGE_REGISTRY)/$(IMAGE_ORG)/operator-catalog:v$(VERSION)

.PHONY: help build push install uninstall deploy-sample lint bundle-validate bundle-build bundle-push catalog-build catalog-push test-bundle

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	    awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'

build: ## Build the operator container image
	IMAGE_REGISTRY=$(IMAGE_REGISTRY) IMAGE_ORG=$(IMAGE_ORG) IMAGE_TAG=$(IMAGE_TAG) \
	    ./hack/build-push.sh

push: build ## Build and push the operator image

install: ## Install operator on the connected OpenShift cluster (requires cluster-admin)
	OPERATOR_IMAGE=$(OPERATOR_IMAGE) ./hack/install.sh

uninstall: ## Remove operator from the cluster
	./hack/uninstall.sh

deploy-sample: ## Apply the basic sample TinycodeInstance
	oc apply -f config/samples/tinycode_v1alpha1_basic.yaml

lint: ## Lint Helm chart
	helm lint helm-charts/tinycode

bundle-validate: ## Validate the OLM bundle
	operator-sdk bundle validate ./bundle --select-optional suite=operatorframework

bundle-build: ## Build the OLM bundle image
	podman build -f bundle.Dockerfile -t $(BUNDLE_IMAGE) .

bundle-push: bundle-build ## Build and push the OLM bundle image
	podman push $(BUNDLE_IMAGE)

catalog-build: ## Build the catalog image (File-Based Catalog)
	opm index add --bundles $(BUNDLE_IMAGE) --tag $(CATALOG_IMAGE) --container-tool podman

catalog-push: catalog-build ## Build and push the catalog image
	podman push $(CATALOG_IMAGE)

test-bundle: ## Test the bundle locally using operator-sdk
	operator-sdk run bundle $(BUNDLE_IMAGE)
