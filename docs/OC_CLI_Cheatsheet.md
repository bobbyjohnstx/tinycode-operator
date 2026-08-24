# OC CLI Cheatsheet
Source: Red Hat OpenShift Container Platform 4.18

---

## Authentication & Session

```bash
oc login -u <user> -p <password> https://api.<cluster>:6443
oc logout
oc whoami
oc project <project-name>           # switch project
oc projects                         # list all projects
```

---

## Projects & Namespaces

```bash
oc new-project <name>
oc delete project <name>
oc get projects

# Namespace (admin-only, bypasses project template)
oc create namespace <name>
oc delete namespace <name>

# Edit cluster-wide project config (template, self-provisioner)
oc edit projects.config.openshift.io cluster
```

---

## Declarative Resource Management

```bash
# Apply / create from manifest (declarative — preferred)
oc apply -f <file.yaml>
oc apply -f .                        # all files in current dir
oc apply -f . --namespace <ns>
oc apply -R -f <dir>                 # recursive

# Validate without applying
oc apply -f <file> --dry-run=server --validate=true

# Create (imperative — errors if resource exists)
oc create -f <file.yaml>
oc create -R -f <dir>
oc create -f https://example.com/file.yaml

# Replace (full overwrite)
oc replace -f <file.yaml>

# Delete from manifest
oc delete -f <file.yaml>
oc delete -f . --namespace <ns>

# Diff live vs manifest
oc diff -f <file.yaml>

# Patch (inline JSON snippet)
oc patch deployment hello -p \
  '{"spec":{"template":{"spec":{"containers":[{"name":"hello-rhel7","resources":{"requests":{"cpu":"100m"}}}]}}}}'

# Patch from file
oc patch deployment hello --patch-file ~/volume-mount.yaml

# Patch with merge strategy (common for operators/subscriptions)
oc patch <resource> <name> --type merge -p '{"spec":{"field":"value"}}'

# Generate manifest from imperative command (--dry-run=client)
oc create deployment hello-openshift -o yaml \
  --image registry.example.com/redhattraining/hello-world-nginx:v1.0 \
  --save-config \
  --dry-run=client > ~/my-app/example-deployment.yaml

# Interactive edit
oc edit <resource> <name>
oc edit oauth

# Restart pods to pick up config changes (secret/configmap updates)
oc rollout restart deployment/<name>
oc rollout restart deployment/<name> --namespace <ns>

# Explain any field
oc explain deployment.spec.template.spec
```

---

## Kustomize

```bash
# Preview rendered manifests (no apply)
oc kustomize overlay/production

# Apply a kustomization directory
oc apply -k overlay/production

# Delete resources from kustomization
oc delete -k overlay/production
```

---

## Getting & Inspecting Resources

```bash
# Basic get
oc get pods
oc get deployments
oc get services
oc get routes
oc get configmaps
oc get secrets
oc get nodes
oc get all -n <namespace>

# Output formats
oc get <resource> -o yaml
oc get <resource> -o wide
oc get <resource> -o jsonpath='{.items[0].spec.channel}{"\n"}'
oc get <resource> -o NAME

# Filtering
oc get <resource> -n <namespace>
oc get <resource> -l app=<label>
oc get <resource> --all-namespaces
oc get <resource> --sort-by .metadata.creationTimestamp

# Multiple resource types at once
oc get pods,deployments
oc get pod,deployment --namespace <ns>

# Describe (human-readable details + events)
oc describe <resource> <name>
oc describe node/master01
oc describe clusterversion

# Watch (live updates)
watch oc get pods
watch oc get deployments,pods --namespace <ns>

# Get configmaps with --watch flag
oc get configmaps -n <ns> --watch

# Events (great for troubleshooting quota/scheduling failures)
oc get events --sort-by .metadata.creationTimestamp
oc get event -n <ns> --sort-by=metadata.creationTimestamp

# API resources (list all resource types)
oc api-resources --api-group="" --namespaced=true

# List available fields for a resource type
oc explain deployment.spec.template.spec
```

---

## Deployments & Workloads

```bash
# Create deployment (imperative)
oc create deployment <name> --image <image>
oc create deployment test --image registry.example.com/redhattraining/hello-world-nginx

# Set environment variables
oc set env deployment/<name> KEY=value KEY2=value2

# Set resource requests/limits
oc set resources deployment <name> --requests=cpu=1
oc set resources deployment <name> --requests=cpu=1,memory=512Mi --limits=memory=1Gi

# Set service account on deployment
oc set serviceaccount deployment <name> <sa-name>

# Scale
oc scale deployment <name> --replicas=3
oc scale deployment -n <ns> <name> --replicas=1

# Logs
oc logs deployment/<name>
oc logs pod/<pod-name>

# Execute command in pod/deployment
oc exec pod/<name> -- <command>
oc exec deployment/<name> -- curl -sS http://localhost/status
oc exec deploy/client -- curl -s https://server.network-svccerts.svc

# Deploy from app (creates deployment + service)
oc new-app --name httpd registry.example.com/rhscl/httpd-24-rhel7
```

---

## Pods

```bash
oc get pods
oc get pods -n <namespace>
oc get pods -o wide
oc describe pod/<name>
oc logs pod/<name>
oc delete pod/<name>

# Debug a node (runs privileged debug pod on node)
oc debug node/<node-name> -- touch /host/etc/testfile
```

---

## Routes & Services

```bash
oc get routes
oc get route
oc get routes -l app=<name> --namespace <ns>

# Create passthrough TLS route
oc create route passthrough product-https \
  --service product --port 8080 \
  --hostname product.apps.ocp4.example.com

# Expose service as route (edge TLS by default in OCP)
oc expose service <name>

# Annotate a service to get auto-generated TLS cert (service-ca)
oc annotate service <name> \
  service.beta.openshift.io/serving-cert-secret-name=<secret-name>

# Inject cluster CA into a configmap
oc annotate configmap ca-bundle \
  service.beta.openshift.io/inject-cabundle=true
```

---

## Secrets & ConfigMaps

```bash
# Create secrets
oc create secret generic <name> --from-file htpasswd=<file> -n <ns>
oc create secret generic <name> --from-literal KEY=value
oc create secret tls <name> --cert certs/product.pem --key certs/product.key

# Update secret data
oc set data secret/<name> --from-file htpasswd=<file> -n <ns>

# Extract secret to file/stdout
oc extract secret/<name> -n <ns> --to <dir> --confirm
oc extract secret/<name> -n <ns> --to -       # stdout

# Delete secret
oc delete secret <name> -n <ns>

# Get secret with jsonpath (e.g. decode TLS cert)
oc get secret <name> --output="jsonpath={.data.tls\.crt}" | base64 -d | openssl x509 -text

# ConfigMaps
oc create configmap <name>
oc create configmap <name> --from-file <key>=<file>
oc get configmap <name> -o yaml
oc describe configmap <name>
```

---

## RBAC — Users, Roles & Bindings

```bash
# Cluster roles (apply across all projects)
oc adm policy add-cluster-role-to-user cluster-admin <username>
oc adm policy add-cluster-role-to-user <role> <username>
oc adm policy remove-cluster-role-from-user <role> <username>

# Cluster roles to groups
oc adm policy add-cluster-role-to-group \
  --rolebinding-name self-provisioners \
  self-provisioner system:authenticated:oauth
oc adm policy remove-cluster-role-from-group \
  self-provisioner system:authenticated:oauth

# Namespace/project roles
oc policy add-role-to-user admin leader          # project admin
oc policy add-role-to-user basic-user dev -n wordpress
oc policy add-role-to-group edit dev-group
oc policy add-role-to-group view qa-group

# Check who can perform an action
oc adm policy who-can delete user

# View role bindings (filter out system service accounts)
oc get rolebindings -o wide | grep -v '^system:'
oc get clusterrolebinding -o wide | grep -E 'ROLE|self-provisioner'
oc describe clusterrolebindings self-provisioners

# Edit a cluster role binding
oc edit clusterrolebinding self-provisioners

# Patch to remove subjects (disable self-provisioning)
oc patch clusterrolebinding.rbac self-provisioners \
  -p '{"subjects": null}'

# Prevent auto-restore of role binding on API server restart
oc annotate clusterrolebinding/self-provisioners \
  --overwrite rbac.authorization.kubernetes.io/autoupdate=false

# Default roles reference:
# cluster-admin   — full superuser access
# admin           — manage all project resources + grant access
# edit            — create/change/delete resources (not quota/limits)
# view            — read-only project resources
# self-provisioner — create own projects
# basic-user      — get basic project/user info
# cluster-status  — view cluster status
# cluster-reader  — read most objects cluster-wide
```

---

## Users, Groups & Identity

```bash
# Users
oc get users
oc delete user <name>
oc delete user --all

# Identities
oc get identity
oc get identity -o NAME | grep <name>
oc delete identity "myusers:<username>"
oc delete identity --all

# Groups
oc get groups
oc adm groups new <group-name>
oc adm groups add-users <group-name> <user1> <user2>
oc describe group <group-name>

# HTPasswd identity provider — edit OAuth config
oc edit oauth

# Get OAuth resource
oc get oauth cluster -o yaml > oauth.yaml
oc replace -f oauth.yaml

# Watch auth pods restart after OAuth change
watch oc get pod -n openshift-authentication
```

---

## Resource Quotas & LimitRanges

```bash
# Create quota (imperative)
oc create quota <name> --hard=count/pods=1
oc create quota one-cpu --hard=requests.cpu=1
oc create quota memory --hard=requests.memory=2Gi,limits.memory=4Gi -n <ns>

# Cluster-wide quota (across namespaces by label)
oc create clusterresourcequota example \
  --project-label-selector=group=dev \
  --hard=requests.cpu=10

# View quotas
oc get quota
oc get resourcequota
oc describe quota
oc describe resourcequota -n <ns> <name>
oc get limitrange
oc describe limitrange

# View cluster resource quota applied to a namespace
oc describe AppliedClusterResourceQuota -n <ns>

# Object count quota syntax:
#   count/pods           — pods
#   count/deployments.apps  — deployments
#   count/services       — services

# Bootstrap project template (admin)
oc adm create-bootstrap-project-template -o yaml > template.yaml
oc create -f template.yaml -n openshift-config
oc edit projects.config.openshift.io cluster   # set projectRequestTemplate
```

---

## Application Security — SCCs & Service Accounts

```bash
# Create service account
oc create sa <name>
# shorthand:
oc create serviceaccount <name>

# Assign SCC to a service account
oc adm policy add-scc-to-user anyuid -z <sa-name>
oc adm policy add-scc-to-user privileged -z <sa-name>

# Find which SCC allows a deployment to run
oc adm policy scc-subject-review -f <manifest.yaml>

# Assign service account to deployment
oc set serviceaccount deployment <name> <sa-name>

# Assign cluster role to service account (-z = service account in current namespace)
oc adm policy add-cluster-role-to-user project-cleaner -z project-cleaner-sa
oc adm policy add-clusterrole-to-user <role> -z <sa-name>
```

---

## Operators & OLM

```bash
# List available operators
oc get catalogsource -n openshift-marketplace
oc get packagemanifests

# Inspect an operator before installing
oc describe packagemanifest <operator-name> -n openshift-marketplace
oc describe packagemanifest file-integrity-operator

# After creating subscription — check operator status
oc describe operator <name>                        # shows install plan
oc get csv -n <operator-namespace>                 # cluster service versions
oc get all -n <operator-namespace>                 # workloads

# Install plans (Manual approval)
oc get installplan -n <ns>
oc get installplan <name> -n <ns> -o jsonpath='{.spec}{"\n"}'

# Approve a pending install plan
oc patch installplan <name> --type merge -p \
  '{"spec":{"approved":true}}' -n <ns>

# Change operator update channel
oc patch subscription <name> -n <ns> --type merge \
  -p '{"spec":{"channel":"<new-channel>"}}'

# List CRDs owned by an operator's CSV
oc get csv <csv-name> \
  -o jsonpath="{.spec.customresourcedefinitions.owned[*].name}{'\n'}"

# Explain available CRDs
oc explain <custom-resource-kind>
```

---

## Cluster Updates

```bash
# Check current version and available updates
oc get clusterversion
oc adm upgrade

# View current update channel
oc get clusterversion -o jsonpath='{.items[0].spec.channel}{"\n"}'

# Change update channel
oc patch clusterversion version --type="merge" \
  --patch '{"spec":{"channel":"fast-4.18"}}'

# Apply latest available update
oc adm upgrade --to-latest=true

# Apply specific version
oc adm upgrade --to=4.18.26

# Monitor update progress
watch oc get clusterversion,clusteroperators

# View update history
oc describe clusterversion

# Pause machine health checks before update (multi-node clusters)
oc get machinehealthcheck -n openshift-machine-api
oc annotate machinehealthcheck -n openshift-machine-api <name> \
  cluster.x-k8s.io/paused=""
# Remove annotation after update:
oc annotate machinehealthcheck -n openshift-machine-api <name> \
  cluster.x-k8s.io/paused-

# Watch API server pods restart (after oauth/project config changes)
watch oc get pod -n openshift-apiserver
```

---

## Nodes

```bash
oc get nodes
oc describe node/<name>
oc adm top node                    # CPU/memory usage per node
oc debug node/<name> -- <command>  # privileged debug pod on node
```

---

## Troubleshooting Patterns

```bash
# Pod not starting — check events
oc get events --sort-by .metadata.creationTimestamp
oc describe pod/<name>
oc logs pod/<name>

# CrashLoopBackOff after config change — force restart
oc rollout restart deployment/<name>

# Quota exceeded — check events and quota usage
oc get events --sort-by .metadata.creationTimestamp
oc describe resourcequota

# Pending pods — check node capacity
oc adm top node
oc describe node/<name>            # shows Allocated resources

# Operator not healthy — check install plan and CSV
oc describe operator <name>
oc get installplan -n <ns>
oc describe operator <name> | grep InstallSucceeded

# Network policy blocking traffic — test with exec + curl
oc exec deployment/<name> -- curl -s http://other-service:port

# TLS cert verification
oc exec <pod> -- openssl s_client -connect server.namespace.svc:443

# Check Multus secondary network annotation on pod
oc get pod <name> \
  -o jsonpath='{.metadata.annotations.k8s\.v1\.cni\.cncf\.io/network-status}'
```

---

## Common JSONPath Patterns

```bash
# Get a specific field
oc get clusterversion -o jsonpath='{.items[0].spec.channel}'

# Get pod resource limits
oc get pod -n <ns> \
  -o jsonpath='{.items[0].spec.containers[0].resources}'

# Get all CRD names from a CSV
oc get csv <name> \
  -o jsonpath="{.spec.customresourcedefinitions.owned[*].name}{'\n'}"

# Get install plan spec
oc get installplan <name> -n <ns> -o jsonpath='{.spec}{"\n"}'
```

---

## Key Annotation Reference

| Annotation | Use |
|-----------|-----|
| `service.beta.openshift.io/serving-cert-secret-name: <secret>` | Auto-generate TLS cert for service |
| `service.beta.openshift.io/inject-cabundle: "true"` | Inject cluster CA into configmap |
| `rbac.authorization.kubernetes.io/autoupdate: "false"` | Prevent role binding auto-restore |
| `cluster.x-k8s.io/paused: ""` | Pause machine health check |
| `k8s.v1.cni.cncf.io/networks: custom` | Attach Multus secondary network |

---

## Default RBAC Roles Quick Reference

| Role | Scope | Can Do |
|------|-------|--------|
| `cluster-admin` | Cluster | Everything |
| `cluster-reader` | Cluster | Read most objects |
| `cluster-status` | Cluster | View cluster status |
| `self-provisioner` | Cluster | Create own projects |
| `admin` | Project | Manage all project resources + grant access |
| `edit` | Project | Create/change/delete deployments, services |
| `view` | Project | Read-only |
| `basic-user` | Project | Basic info about projects/users |

---

## Quota Resource Syntax

```
count/pods                  — pod count
count/deployments.apps      — deployment count  
count/services              — service count
requests.cpu                — CPU request total
limits.cpu                  — CPU limit total
requests.memory             — memory request total
limits.memory               — memory limit total
```

---

*Covers: Manifests, Kustomize, Helm, Auth/RBAC, Network Security, Quotas, Operators, SCCs, Updates*
