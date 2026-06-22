IMAGE_REGISTRY ?= quay.io
IMAGE_ORG      ?= tinycode
IMAGE_TAG      ?= latest
OPERATOR_IMAGE ?= $(IMAGE_REGISTRY)/$(IMAGE_ORG)/operator:$(IMAGE_TAG)

.PHONY: help build push install uninstall deploy-sample lint

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
