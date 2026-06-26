# Shared Team Workspace — Multi-User TinycodeInstance with RWX PVC

**Issue:** tinycode-operator #7
**Status:** Draft
**Created:** 2026-06-25

## Context

Currently, each TinycodeInstance is a single-user deployment with ReadWriteOnce PVCs. While the operator supports `replicas: 1-10` in the CRD schema, setting `replicas > 1` breaks because the RWO PVC can only be mounted by one pod. A shared workspace mode would allow multiple users to access the same tinycode instance through the web UI, sharing a `/projects` workspace backed by a ReadWriteMany (RWX) PVC.

Session isolation is handled at the tinycode application layer — each browser connection gets an independent AI session. The shared element is the filesystem: all users see and edit the same project files.

## Work Objectives

- Users can configure a TinycodeInstance with RWX storage for the projects PVC
- Multiple replicas can mount the same projects PVC simultaneously
- Each pod gets ephemeral session data (SQLite per-pod, not shared)
- Route session affinity keeps WebSocket/SSE connections sticky to one pod
- Operator validates that `replicas > 1` requires RWX access mode

## Guardrails

**Must Have:**
- Projects PVC access mode configurable (RWO default, RWX opt-in)
- Operator validation: `replicas > 1` rejected unless `projectsAccessMode: ReadWriteMany`
- Data PVC uses `emptyDir` when `replicas > 1` (SQLite cannot be shared)
- Route session affinity annotation when `replicas > 1`
- Clear documentation that concurrent file edits are not conflict-resolved

**Must NOT Have:**
- StatefulSet migration (keep Deployment, use emptyDir for per-pod data)
- Collaborative editing / CRDT (tinycode does not support this)
- Automatic StorageClass validation (warn only, do not block)
- Data PVC sharing across pods (SQLite corruption risk)

## Assumptions

- `emptyDir` for per-pod data in multi-replica mode is acceptable. Session history, config, and SQLite DB are ephemeral and lost on pod restart. This is a reasonable tradeoff for shared workspace v1. Users who need persistent sessions should use `replicas: 1`.
- ODF CephFS or equivalent RWX-capable StorageClass is available on the target cluster. The operator warns but does not validate StorageClass capabilities.
- tinycode handles multiple concurrent sessions correctly (each session is independent at the application layer). File-level conflicts from concurrent edits are the user's responsibility.

## Task Flow

### Step 1: CRD Schema Update
Add `spec.storage.projectsAccessMode` to the CRD at `config/crd/tinycode.dev_tinycodeinstances.yaml`.

New field under `spec.storage`:
```yaml
projectsAccessMode:
  type: string
  enum: ["ReadWriteOnce", "ReadWriteMany"]
  default: "ReadWriteOnce"
```

No changes to `spec.replicas` — it already exists with `minimum: 1, maximum: 10`.

**Acceptance:** `kubectl apply` with a CR containing `spec.storage.projectsAccessMode: ReadWriteMany` succeeds. A CR with `spec.storage.projectsAccessMode: ReadWriteOther` is rejected at admission.

### Step 2: Operator Validation Logic
Add `validate_shared_workspace()` to `operator/main.py`, called from `reconcile()` before `run_helm()`.

Validations:
1. If `replicas > 1` AND `projectsAccessMode` is not `ReadWriteMany` AND `hostPath` is not enabled, reject with `StorageConfigError` condition ("replicas > 1 requires projectsAccessMode: ReadWriteMany or hostPath")
2. If `projectsAccessMode: ReadWriteMany` is set, check if the PVC `<name>-projects` already exists with `ReadWriteOnce`. If so, set a `StorageMigrationRequired` warning condition ("existing PVC has ReadWriteOnce; delete PVC manually to recreate with ReadWriteMany"). Do NOT delete the PVC automatically.
3. If `projectsAccessMode: ReadWriteMany` is set, set a `StorageInfo` condition ("ReadWriteMany requires an RWX-capable StorageClass such as CephFS or NFS")

Surface failures via `set_status()` with appropriate conditions, same pattern as existing validation functions.

**Acceptance:** A CR with `replicas: 3` and default RWO access mode sets status phase to `Failed`. A CR with `replicas: 3` and `projectsAccessMode: ReadWriteMany` proceeds to Helm install.

### Step 3: Helm Values, PVC Template, and Deployment Changes
Update three files in `helm-charts/tinycode/`:

**values.yaml additions:**
```yaml
storage:
  # ... existing fields ...
  projectsAccessMode: "ReadWriteOnce"    # new field
```

**pvc.yaml changes:**
Replace hardcoded `ReadWriteOnce` on the projects PVC with the configurable value:
```yaml
# Projects PVC
spec:
  accessModes:
    - {{ .Values.storage.projectsAccessMode }}
```
Data PVC remains hardcoded to `ReadWriteOnce` (SQLite).

**deployment.yaml changes:**
Conditionally use `emptyDir` for data volume when `replicas > 1`:
```yaml
volumes:
  {{- if .Values.storage.hostPath.enabled }}
    # ... existing hostPath logic unchanged ...
  {{- else }}
    {{- if gt (int .Values.replicas) 1 }}
    - name: data
      emptyDir: {}
    {{- else }}
    - name: data
      persistentVolumeClaim:
        claimName: {{ .Values.instanceName }}-data
    {{- end }}
    - name: projects
      persistentVolumeClaim:
        claimName: {{ .Values.instanceName }}-projects
  {{- end }}
```

**Acceptance:** `helm template` with `replicas=3, storage.projectsAccessMode=ReadWriteMany` renders the projects PVC with RWX access mode and data volume as emptyDir. With `replicas=1`, data volume uses the PVC (backwards compatible).

### Step 4: Route Session Affinity
Update `helm-charts/tinycode/templates/route.yaml` to add session affinity annotation when `replicas > 1`.

```yaml
metadata:
  name: {{ .Values.instanceName }}-tinycode
  annotations:
    {{- if gt (int .Values.replicas) 1 }}
    haproxy.router.openshift.io/balance: source
    haproxy.router.openshift.io/disable_cookies: "false"
    {{- end }}
```

This ensures WebSocket and SSE connections from the same client IP are routed to the same pod, preventing session disruption during a conversation.

**Acceptance:** Route manifest includes affinity annotations when replicas > 1. Route manifest has no affinity annotations when replicas = 1 (backwards compatible).

### Step 5: Operator helm_values_for_spec Update, Samples, and Documentation

**operator/main.py update:**
```python
storage_spec = spec.get("storage", {})
values["storage"]["projectsAccessMode"] = storage_spec.get(
    "projectsAccessMode", "ReadWriteOnce"
)
```

**New sample CR** at `config/samples/tinycode_v1alpha1_shared.yaml`:
```yaml
apiVersion: tinycode.dev/v1alpha1
kind: TinycodeInstance
metadata:
  name: team-workspace
  namespace: tinycode-team
spec:
  replicas: 2
  storage:
    projectsSize: "50Gi"
    projectsAccessMode: ReadWriteMany
    storageClassName: ocs-storagecluster-cephfs
  auth:
    passwordSecret: team-password
```

**Documentation updates:**
- `README.md`: Shared workspace section with usage, StorageClass requirements, limitations
- Document that session data is ephemeral when `replicas > 1` (SQLite in emptyDir)
- Document that concurrent file edits are not conflict-resolved
- Document recommended StorageClasses (CephFS, NFS, EFS)
- Recommend `auth.passwordSecret` for shared instances (mandatory for team use)

**Acceptance:** Sample CR applies cleanly. README documents the feature. Operator maps new spec field to Helm values correctly.

## Interaction with GitOps Mode (Issue #6)

When both features are enabled (`spec.git.url` + `spec.storage.projectsAccessMode: ReadWriteMany` + `replicas > 1`):

- Multiple pods starting simultaneously will all run the git init container
- The init container script must handle the race: second pod finds `/projects/.git` already exists and skips clone
- This is already handled by the init container logic in the GitOps plan (checks for existing `.git` directory)
- `pullOnRestart` with multiple pods is safe: `git pull --ff-only` is idempotent and concurrent pulls on a shared RWX volume are safe if no local changes exist

No additional changes needed beyond what each plan already specifies.

## Security Considerations

- Shared PVC means any user's session can read/write any file in `/projects`
- Auth password should be mandatory for shared instances (document as best practice, do not enforce)
- SQLite per-pod (emptyDir) means session history is not shared between pods — one user cannot see another's AI conversation
- Route session affinity prevents session hijacking across pods

## Success Criteria

- A TinycodeInstance CR with `replicas: 2` and `projectsAccessMode: ReadWriteMany` deploys successfully with both pods running
- Both pods mount the same projects PVC and can see each other's file changes
- Each pod has its own ephemeral data volume (session isolation)
- WebSocket connections stay sticky to one pod via Route affinity
- `replicas > 1` with default RWO access mode is rejected with a clear error
- Backwards compatible: existing CRs with `replicas: 1` work identically to before
