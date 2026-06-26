# GitOps Mode — Clone Git Repo into PVC on Startup

**Issue:** tinycode-operator #6
**Status:** Draft
**Created:** 2026-06-25

## Context

Without a host filesystem mount, the tinycode container's `/projects` PVC starts empty. Users must manually copy files in or use the tinycode shell to clone repos. A GitOps mode would clone a specified repository into the PVC at pod startup via an init container, making tinycode immediately useful for working on real code.

The container's `entrypoint.sh` already runs `git init` in `/projects` if no `.git` directory exists (lines 39-48). An init container that clones a repo before entrypoint runs will leave a `.git` directory, causing entrypoint to correctly skip its own init.

## Work Objectives

- Users can specify a git repo URL in the TinycodeInstance CR spec
- The repo is cloned into `/projects` before the tinycode container starts
- Private repos are supported via SSH keys or HTTPS tokens
- On pod restart, optionally pull latest changes (without discarding local work)

## Guardrails

**Must Have:**
- Init container clones repo before main container starts
- Credentials mounted read-only, never logged or exposed in status
- Clone failure prevents main container startup (init container blocks)
- Mutual exclusivity with `hostPath` mode (operator rejects both together)
- URL scheme restricted to `https://`, `ssh://`, `git://` (no `file://`)
- Non-root execution matching existing SCC constraints (UID 1001, GID 0)

**Must NOT Have:**
- Continuous git sync / sidecar (future enhancement, not this issue)
- Git submodule support (document as unsupported in v1)
- Git LFS support (document as unsupported in v1)
- Force-reset of uncommitted changes on pullOnRestart

## Assumptions

- The tinycode container image includes `git` (entrypoint.sh already uses it at line 39). The init container reuses the tinycode image to avoid maintaining a separate image and to guarantee UID/GID compatibility.
- Shallow clone (`--depth 1`) is the default to minimize bandwidth and disk. Users who need full history can run `git fetch --unshallow` inside tinycode.
- Standard Kubernetes secret types are used: `kubernetes.io/ssh-auth` (key: `ssh-privatekey`) for SSH, `kubernetes.io/basic-auth` (keys: `username`, `password`) for HTTPS.

## Task Flow

### Step 1: CRD Schema Update
Add `spec.git` to the CRD at `config/crd/tinycode.dev_tinycodeinstances.yaml`.

New fields under `spec.git` (all optional, `git` itself is optional):
```yaml
git:
  type: object
  properties:
    url:
      type: string
      pattern: "^(https?|ssh|git)://"
    branch:
      type: string
      default: ""        # empty = repo default branch
    credentialsSecret:
      type: string       # name of Secret in same namespace
    pullOnRestart:
      type: boolean
      default: false
    depth:
      type: integer
      minimum: 0          # 0 = full clone
      default: 1          # shallow by default
```

**Acceptance:** `kubectl apply` with a CR containing `spec.git.url: "https://..."` succeeds. A CR with `spec.git.url: "file:///etc/passwd"` is rejected at admission.

### Step 2: Operator Validation Logic
Add `validate_git_spec()` to `operator/main.py`, called from `reconcile()` before `run_helm()`.

Validations:
1. If `spec.git.url` is set AND `spec.storage.hostPath.path` is set, reject with `GitConfigError` condition ("git and hostPath are mutually exclusive")
2. If `spec.git.credentialsSecret` is set, verify the Secret exists in the same namespace (same pattern as `validate_cluster_admin()`)
3. If Secret exists, verify it contains expected keys based on type (`ssh-privatekey` for ssh-auth, `username`+`password` for basic-auth)
4. Do NOT log or expose secret values in status conditions

Surface failures via `set_status(phase="Failed", conditions=[{type: "GitConfigError", ...}])`.

**Acceptance:** A CR with `credentialsSecret: "nonexistent"` sets status phase to `Failed` with a clear message. A CR with both `hostPath` and `git` is rejected.

### Step 3: Helm Values and Init Container Template
Update `helm-charts/tinycode/values.yaml` and `helm-charts/tinycode/templates/deployment.yaml`.

**values.yaml additions:**
```yaml
git:
  enabled: false
  url: ""
  branch: ""
  credentialsSecret: ""
  pullOnRestart: false
  depth: 1
```

**deployment.yaml changes:**
Add an init container block (conditionally rendered when `git.enabled`):
```yaml
{{- if .Values.git.enabled }}
initContainers:
  - name: git-init
    image: {{ .Values.image }}      # reuse tinycode image
    command: ["/bin/sh", "-c"]
    args:
      - |
        set -e
        CLONE_DIR="/projects"
        
        # Configure git credentials if secret is mounted
        {{- if .Values.git.credentialsSecret }}
        if [ -f /git-credentials/ssh-privatekey ]; then
          mkdir -p ~/.ssh
          cp /git-credentials/ssh-privatekey ~/.ssh/id_rsa
          chmod 600 ~/.ssh/id_rsa
          export GIT_SSH_COMMAND="ssh -i ~/.ssh/id_rsa -o StrictHostKeyChecking=accept-new"
        elif [ -f /git-credentials/username ]; then
          CRED_USER=$(cat /git-credentials/username)
          CRED_PASS=$(cat /git-credentials/password)
          git config --global credential.helper '!f() { echo "username=${CRED_USER}"; echo "password=${CRED_PASS}"; }; f'
        fi
        {{- end }}
        
        if [ -d "$CLONE_DIR/.git" ]; then
          # Repo already exists
          cd "$CLONE_DIR"
          REMOTE_URL=$(git remote get-url origin 2>/dev/null || echo "")
          if [ -z "$REMOTE_URL" ]; then
            # Empty init repo from entrypoint.sh — remove and clone fresh
            rm -rf "$CLONE_DIR/.git"
            timeout 300 git clone --depth {{ .Values.git.depth }} \
              {{- if .Values.git.branch }} --branch {{ .Values.git.branch }}{{- end }} \
              {{ .Values.git.url }} "$CLONE_DIR"
          elif [ "{{ .Values.git.pullOnRestart }}" = "true" ]; then
            # Pull latest (ff-only to avoid destroying local changes)
            echo "Pulling latest changes..."
            git fetch origin
            git pull --ff-only || echo "WARNING: pull failed (likely local changes). Skipping."
          else
            echo "Repo exists, pullOnRestart=false. Skipping."
          fi
        else
          # Fresh clone
          timeout 300 git clone --depth {{ .Values.git.depth }} \
            {{- if .Values.git.branch }} --branch {{ .Values.git.branch }}{{- end }} \
            {{ .Values.git.url }} "$CLONE_DIR"
        fi
    securityContext:
      allowPrivilegeEscalation: false
      runAsNonRoot: true
      capabilities:
        drop:
          - ALL
    resources:
      requests:
        cpu: "100m"
        memory: "128Mi"
      limits:
        cpu: "500m"
        memory: "512Mi"
    volumeMounts:
      - name: projects
        mountPath: /projects
      {{- if .Values.git.credentialsSecret }}
      - name: git-credentials
        mountPath: /git-credentials
        readOnly: true
      {{- end }}
{{- end }}
```

Add git-credentials volume (conditionally):
```yaml
{{- if and .Values.git.enabled .Values.git.credentialsSecret }}
- name: git-credentials
  secret:
    secretName: {{ .Values.git.credentialsSecret }}
    defaultMode: 0400
{{- end }}
```

**Acceptance:** `helm template` with `git.enabled=true, git.url=https://...` renders init container. Without git values, no init container is rendered.

### Step 4: Operator helm_values_for_spec Update
Update `helm_values_for_spec()` in `operator/main.py` to map `spec.git` fields to Helm values.

```python
git_spec = spec.get("git", {})
values["git"] = {
    "enabled": bool(git_spec.get("url")),
    "url": git_spec.get("url", ""),
    "branch": git_spec.get("branch", ""),
    "credentialsSecret": git_spec.get("credentialsSecret", ""),
    "pullOnRestart": git_spec.get("pullOnRestart", False),
    "depth": git_spec.get("depth", 1),
}
```

**Acceptance:** A CR with `spec.git.url` produces a Helm release with the init container. A CR without `spec.git` produces no init container (backwards compatible).

### Step 5: Sample CRs, Documentation, and Testing

- Add `config/samples/tinycode_v1alpha1_gitops.yaml` — example CR with public repo
- Add `config/samples/tinycode_v1alpha1_gitops_private.yaml` — example CR with credentialsSecret
- Update `CONTAINER.md` to document the git init container interface
- Update `README.md` with GitOps mode section (usage, secret format, limitations)
- Document limitations: no submodules, no LFS, no continuous sync in v1
- Test on cluster: public repo clone, private repo clone (SSH + HTTPS), pullOnRestart with clean tree, pullOnRestart with dirty tree, hostPath+git rejection

**Acceptance:** Sample CRs apply cleanly. README documents the feature with examples. All test scenarios pass on a live cluster.

## Security Considerations

- Git credentials are mounted read-only via Kubernetes Secret volumes (mode 0400)
- SSH keys are copied to a tmpfs-backed home directory, never written to the PVC
- HTTPS credentials are passed via git credential helper, never written to disk
- URL scheme validation prevents `file://` and local path access
- Init container runs with same restrictive security context as main container
- Operator never logs or exposes secret contents in CR status

## Success Criteria

- A TinycodeInstance CR with `spec.git.url` results in a pod that starts with the repo cloned into `/projects`
- Private repos work with both SSH and HTTPS credentials
- Pod restarts with `pullOnRestart: true` pull latest without discarding local changes
- Pod restarts with `pullOnRestart: false` skip git operations entirely
- Specifying both `hostPath` and `git` is rejected with a clear error
- Backwards compatible: existing CRs without `spec.git` work identically to before
