#!/usr/bin/env python3
"""
tinycode-operator — Kubernetes operator for managing TinycodeInstance CRs.

Built with kopf (Kubernetes Operator Pythonic Framework).
Reconciles TinycodeInstance CRs by rendering and applying the tinycode Helm chart,
managing SCCs, and updating status conditions.

Usage:
    python main.py  (or via the container entrypoint /manager)
"""

import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import kopf
import kubernetes
import yaml

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("tinycode-operator")

# ── Configuration ─────────────────────────────────────────────────────────────

HELM_CHART_PATH = os.environ.get("HELM_CHART_PATH", "/helm-charts/tinycode")
OPERATOR_NAMESPACE = os.environ.get("OPERATOR_NAMESPACE", "tinycode-operator-system")
GROUP = "tinycode.dev"
VERSION = "v1alpha1"
PLURAL = "tinycodeinstances"

# SCC selection logic: restricted < hostpath < shell (most privilege)
SCC_RESTRICTED = "tinycode-restricted"
SCC_HOSTPATH = "tinycode-hostpath"
SCC_SHELL = "tinycode-shell"

# ── Kubernetes clients ────────────────────────────────────────────────────────

try:
    kubernetes.config.load_incluster_config()
    log.info("Loaded in-cluster kubeconfig")
except kubernetes.config.ConfigException:
    kubernetes.config.load_kube_config()
    log.info("Loaded local kubeconfig (development mode)")

core_v1 = kubernetes.client.CoreV1Api()
apps_v1 = kubernetes.client.AppsV1Api()
custom_api = kubernetes.client.CustomObjectsApi()
rbac_v1 = kubernetes.client.RbacAuthorizationV1Api()

_dynamic_client = None

def get_dynamic_client():
    """Return a cached DynamicClient instance."""
    global _dynamic_client
    if _dynamic_client is None:
        _dynamic_client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    return _dynamic_client


# ── Helpers ───────────────────────────────────────────────────────────────────

def validate_vllm_url(url: str) -> bool:
    """Reject URLs targeting metadata endpoints or loopback."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        return False
    BLOCKED_HOSTS = {"169.254.169.254", "metadata.google.internal", "100.100.100.200"}
    if hostname in BLOCKED_HOSTS:
        return False
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_loopback or ip.is_link_local:
            return False
    except ValueError:
        pass
    return True


def scc_name_for_spec(spec: dict) -> str:
    """Return the least-privilege SCC name for this instance spec."""
    if spec.get("shell", {}).get("enabled", False):
        return SCC_SHELL
    if spec.get("storage", {}).get("hostPath", {}).get("path"):
        return SCC_HOSTPATH
    return SCC_RESTRICTED


def probe_vllm_models(url: str, timeout: int = 5) -> list[dict]:
    """
    Probe a vLLM instance for available models.
    Returns a list of {id, max_model_len} dicts. Returns empty list on failure.
    """
    import urllib.request
    import urllib.error
    import json as json_mod

    # Normalize URL: strip trailing slash, ensure /v1 suffix
    url = url.rstrip("/")
    if not url.endswith("/v1"):
        url = f"{url}/v1"

    if not validate_vllm_url(url):
        log.warning("Rejected vLLM URL (metadata endpoint or loopback): %s", url)
        return []

    models_url = f"{url}/models"
    try:
        req = urllib.request.Request(models_url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json_mod.loads(resp.read())
        models = []
        for entry in data.get("data", []):
            models.append({
                "id": entry.get("id", ""),
                "max_model_len": entry.get("max_model_len", 0),
            })
        return models
    except Exception as exc:
        log.warning("Failed to probe vLLM at %s: %s", url, exc)
        return []


def build_vllm_config(spec: dict, namespace: str) -> dict | None:
    """
    Build tinycode provider config from spec.vllm array.
    Returns a dict suitable for merging into the tinycode config.json, or None if no vllm entries.
    """
    vllm_entries = spec.get("vllm", [])
    if not vllm_entries:
        return None

    import math

    providers = {}
    default_model = spec.get("model", "")

    for entry in vllm_entries:
        name = entry.get("name", "")
        url = entry.get("url", "").rstrip("/")
        if not url.endswith("/v1"):
            url = f"{url}/v1"

        user_models = entry.get("models", {})
        probed_models = probe_vllm_models(url)

        models = {}
        for pm in probed_models:
            model_id = pm["id"]
            max_len = pm["max_model_len"]

            # Check for user override
            if model_id in user_models:
                ctx = user_models[model_id].get("contextLimit")
                out = user_models[model_id].get("outputLimit")
                if ctx and out:
                    models[model_id] = {"contextLimit": ctx, "outputLimit": out}
                    continue

            # Auto-detect: 80/20 split
            if max_len > 0:
                context = math.floor(max_len * 0.8)
                output = min(4096, math.floor(max_len * 0.2))
                models[model_id] = {"contextLimit": context, "outputLimit": output}

        # Add user-defined models not in probed list
        for model_id, limits in user_models.items():
            if model_id not in models:
                ctx = limits.get("contextLimit", 8192)
                out = limits.get("outputLimit", 4096)
                models[model_id] = {"contextLimit": ctx, "outputLimit": out}

        if models:
            providers[name] = {
                "type": "openai-compatible",
                "url": url,
                "models": models,
            }

    if not providers:
        return None

    config = {"providers": providers}
    if default_model:
        config["model"] = default_model

    return config


def helm_values_for_spec(name: str, namespace: str, spec: dict) -> dict:
    """Build Helm values from a TinycodeInstanceSpec."""
    import json as json_mod

    storage = spec.get("storage", {})
    host_path = storage.get("hostPath", {})

    values: dict[str, Any] = {
        "instanceName": name,
        "instanceNamespace": namespace,
        "image": spec.get("image", "quay.io/bjohns/tinycode-container:latest"),
        "replicas": spec.get("replicas", 1),
        "resources": spec.get("resources", {
            "limits": {"cpu": "2", "memory": "2Gi"},
            "requests": {"cpu": "200m", "memory": "512Mi"},
        }),
        "storage": {
            "dataSize": storage.get("dataSize", "1Gi"),
            "projectsSize": storage.get("projectsSize", "10Gi"),
            "projectsAccessMode": storage.get("projectsAccessMode", "ReadWriteOnce"),
            "storageClassName": storage.get("storageClassName", ""),
            "hostPath": {
                "enabled": bool(host_path.get("path")),
                "path": host_path.get("path", ""),
                "readOnly": host_path.get("readOnly", False),
            },
        },
        "hostname": spec.get("hostname", ""),
        "tlsTermination": spec.get("tlsTermination", "edge"),
        "ollama": spec.get("ollama", {"enabled": False, "host": "", "models": []}),
        "auth": spec.get("auth", {"passwordSecret": ""}),
        "shell": spec.get("shell", {"enabled": False}),
        "nodeSelector": spec.get("nodeSelector", {}),
        "tolerations": spec.get("tolerations", []),
    }
    cluster_admin = spec.get("clusterAdmin", {})
    values["clusterAdmin"] = {
        "enabled": cluster_admin.get("enabled", False),
        "kubeconfigSecretName": cluster_admin.get("kubeconfigSecretName", ""),
        "kubeconfigSecretKey": cluster_admin.get("kubeconfigSecretKey", "kubeconfig"),
        "ocVersion": cluster_admin.get("ocVersion", "stable"),
        "kubeconfigNamespace": cluster_admin.get("kubeconfigNamespace", ""),
        "clusterRole": cluster_admin.get("clusterRole", ""),
    }

    git = spec.get("git", {})
    values["git"] = {
        "enabled": bool(git.get("url")),
        "url": git.get("url", ""),
        "branch": git.get("branch", ""),
        "credentialsSecret": git.get("credentialsSecret", ""),
        "pullOnRestart": git.get("pullOnRestart", False),
        "depth": git.get("depth", 1),
    }

    # Build vLLM config and merge into configContent
    vllm_config = build_vllm_config(spec, namespace)
    if vllm_config:
        values["configContent"] = json_mod.dumps(vllm_config, indent=2)
    else:
        values["configContent"] = ""

    # Discovery namespaces for cross-namespace vLLM service discovery
    discovery = spec.get("discovery", {})
    discovery_namespaces = discovery.get("namespaces", [])
    values["discovery"] = {
        "namespaces": discovery_namespaces,
    }

    return values


def helm_release_name(name: str, namespace: str) -> str:
    return f"tinycode-{namespace}-{name}"


def run_helm(args: list[str], values: dict, release: str, namespace: str) -> tuple[bool, str]:
    """Run a helm command with the given values. Returns (success, output)."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, prefix="tinycode-values-"
    ) as f:
        yaml.dump(values, f)
        values_file = f.name

    try:
        cmd = [
            "helm", *args,
            release,
            HELM_CHART_PATH,
            "--namespace", namespace,
            "--values", values_file,
            "--wait",
            "--timeout", "5m",
        ]
        log.info("Running helm: %s", " ".join(cmd))
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=360,
        )
        if result.returncode != 0:
            log.error("helm failed: %s", result.stderr)
            return False, result.stderr
        return True, result.stdout
    except subprocess.TimeoutExpired:
        return False, "helm timed out after 360s"
    except Exception as exc:
        return False, str(exc)
    finally:
        Path(values_file).unlink(missing_ok=True)


def helm_release_exists(release: str, namespace: str) -> bool:
    """Return True if the Helm release already exists."""
    result = subprocess.run(
        ["helm", "status", release, "--namespace", namespace],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def ensure_scc_binding(service_account: str, namespace: str, scc_name: str):
    """
    Bind the appropriate tinycode SCC to the instance's ServiceAccount by
    patching the SCC's users list directly — equivalent to:
      oc adm policy add-scc-to-user <scc> system:serviceaccount:<ns>:<sa>

    Custom SCCs in OpenShift do not get an auto-generated system:openshift:scc:*
    ClusterRole, so patching the SCC users field is the correct approach.
    """
    sa_name = f"{service_account}-tinycode"
    sa_ref = f"system:serviceaccount:{namespace}:{sa_name}"

    dyn_client = get_dynamic_client()
    scc_api = dyn_client.resources.get(
        api_version="security.openshift.io/v1",
        kind="SecurityContextConstraints",
    )
    scc = scc_api.get(name=scc_name)
    users = list(scc.users or [])
    if sa_ref in users:
        log.debug("SCC %s already includes %s", scc_name, sa_ref)
        return

    users.append(sa_ref)
    scc_api.patch(
        name=scc_name,
        body={"users": users},
        content_type="application/merge-patch+json",
    )
    log.info("Added %s to SCC %s users", sa_ref, scc_name)


def remove_scc_binding(name: str, namespace: str):
    """Remove the SA from all tinycode SCCs on instance deletion."""
    sa_ref = f"system:serviceaccount:{namespace}:{name}-tinycode"
    try:
        dyn_client = get_dynamic_client()
        scc_api = dyn_client.resources.get(
            api_version="security.openshift.io/v1",
            kind="SecurityContextConstraints",
        )
        for scc_name in [SCC_RESTRICTED, SCC_HOSTPATH, SCC_SHELL]:
            try:
                scc = scc_api.get(name=scc_name)
                users = [u for u in (scc.users or []) if u != sa_ref]
                scc_api.patch(
                    name=scc_name,
                    body={"users": users},
                    content_type="application/merge-patch+json",
                )
            except Exception:
                pass
        log.info("Removed %s from tinycode SCCs", sa_ref)
    except Exception as exc:
        log.warning("Could not remove SCC binding for %s: %s", name, exc)


def check_vllm_tool_calling(namespace: str) -> list[dict]:
    """
    Probe vLLM services in the namespace for tool calling support.
    Returns a list of warnings for services missing --enable-auto-tool-choice.
    Runs entirely inside the cluster — no external network needed.
    """
    warnings = []
    try:
        services = core_v1.list_namespaced_service(namespace)
    except Exception:
        return warnings

    TOOL_CALL_TEST = {
        "model": "test",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "function": {
            "name": "t", "description": "t",
            "parameters": {"type": "object", "properties": {}}
        }}],
        "tool_choice": "auto",
        "max_tokens": 1,
    }

    import urllib.request
    import urllib.error
    import json as json_mod

    for svc in services.items:
        cluster_ip = svc.spec.cluster_ip
        if not cluster_ip or cluster_ip == "None":
            continue
        svc_name = svc.metadata.name
        ports = [p.port for p in (svc.spec.ports or [])] or [8080, 8000, 80]

        for port in ports:
            svc_url = f"http://{cluster_ip}:{port}/v1"
            if not validate_vllm_url(svc_url):
                continue
            models_url = f"{svc_url}/models"
            try:
                req = urllib.request.Request(models_url, headers={"Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=2) as resp:
                    data = json_mod.loads(resp.read())
                if not data.get("data") or data["data"][0].get("owned_by") != "vllm":
                    continue
            except Exception:
                continue

            # Check context window size — warn if too small for coding sessions
            MIN_CONTEXT = 16384
            for model_entry in data.get("data", []):
                max_model_len = model_entry.get("max_model_len", 0)
                if max_model_len and max_model_len < MIN_CONTEXT:
                    msg = (
                        f"vLLM service {svc_name} model {model_entry['id']} has "
                        f"max_model_len={max_model_len} which is below the recommended "
                        f"{MIN_CONTEXT} for coding sessions. "
                        f"Add --kv-cache-dtype fp8 and --max-model-len 32768 to the "
                        f"vLLM deployment args. See docs/rhoai-cluster-setup.md"
                    )
                    log.warning(msg)
                    warnings.append({"service": svc_name, "port": port, "message": msg, "type": "context"})

            # Confirmed vLLM — now test tool calling
            chat_url = f"http://{cluster_ip}:{port}/v1/chat/completions"
            try:
                body = json_mod.dumps(TOOL_CALL_TEST).encode()
                req = urllib.request.Request(
                    chat_url,
                    data=body,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    resp.read()
                # No error — tool calling works
                log.info("vLLM %s/%s port %d supports tool calling", namespace, svc_name, port)
            except urllib.error.HTTPError as exc:
                body_text = exc.read().decode("utf-8", errors="ignore")
                if "enable-auto-tool-choice" in body_text:
                    msg = (
                        f"vLLM service {svc_name} (port {port}) does not support tool calling. "
                        f"Add --enable-auto-tool-choice and --tool-call-parser to its vLLM args. "
                        f"See docs/vllm-tool-calling.md"
                    )
                    log.warning(msg)
                    warnings.append({"service": svc_name, "port": port, "message": msg})
            except Exception:
                pass
            break

    return warnings


def validate_git_spec(name: str, namespace: str, spec: dict) -> list[dict]:
    """
    Validate git spec. Returns a list of warning/error dicts.
    Checks: mutual exclusivity with hostPath, credentialsSecret existence.
    """
    warnings = []
    git = spec.get("git", {})
    if not git.get("url"):
        return warnings

    # Mutual exclusivity: git.url and hostPath.path
    host_path = spec.get("storage", {}).get("hostPath", {})
    if git.get("url") and host_path.get("path"):
        warnings.append({
            "type": "error",
            "message": "spec.git.url and spec.storage.hostPath.path are mutually exclusive"
        })
        return warnings

    # Verify credentialsSecret if set
    creds_secret = git.get("credentialsSecret", "")
    if creds_secret:
        try:
            log.info(f"Reading Secret '{creds_secret}' in namespace '{namespace}' for git_credentials")
            secret = core_v1.read_namespaced_secret(creds_secret, namespace)
            # Check for expected keys
            data_keys = set((secret.data or {}).keys())
            has_https = {"username", "password"}.issubset(data_keys)
            has_ssh = "ssh-privatekey" in data_keys
            if not has_https and not has_ssh:
                warnings.append({
                    "type": "warning",
                    "message": f"Secret '{creds_secret}' should contain either (username, password) for HTTPS or (ssh-privatekey) for SSH"
                })
        except kubernetes.client.exceptions.ApiException as exc:
            if exc.status == 404:
                warnings.append({
                    "type": "error",
                    "message": f"Git credentialsSecret '{creds_secret}' not found in namespace '{namespace}'"
                })
            else:
                warnings.append({
                    "type": "error",
                    "message": f"Could not read Secret '{creds_secret}': {exc.reason}"
                })

    return warnings


def validate_shared_workspace(name: str, namespace: str, spec: dict) -> list[dict]:
    """
    Validate shared workspace configuration. Returns a list of warning/error dicts.
    Checks: replicas > 1 requires ReadWriteMany or hostPath.
    """
    warnings = []
    replicas = spec.get("replicas", 1)
    storage = spec.get("storage", {})
    access_mode = storage.get("projectsAccessMode", "ReadWriteOnce")
    host_path_enabled = bool(storage.get("hostPath", {}).get("path"))

    if replicas > 1 and access_mode != "ReadWriteMany" and not host_path_enabled:
        warnings.append({
            "type": "error",
            "message": f"replicas={replicas} requires storage.projectsAccessMode=ReadWriteMany or storage.hostPath.enabled=true"
        })

    return warnings


def validate_cluster_admin(name: str, namespace: str, spec: dict) -> list[dict]:
    """
    Validate clusterAdmin spec. Returns a list of warning/error dicts.
    Checks: Secret existence, key presence, multiple contexts, short-lived tokens.
    """
    warnings = []
    cluster_admin = spec.get("clusterAdmin", {})
    if not cluster_admin.get("enabled", False):
        return warnings

    secret_name = cluster_admin.get("kubeconfigSecretName", "")
    secret_key = cluster_admin.get("kubeconfigSecretKey", "kubeconfig")

    if not secret_name:
        warnings.append({"type": "error", "message": "clusterAdmin.enabled is true but kubeconfigSecretName is not set"})
        return warnings

    try:
        log.info(f"Reading Secret '{secret_name}' in namespace '{namespace}' for kubeconfig_validation")
        secret = core_v1.read_namespaced_secret(secret_name, namespace)
    except kubernetes.client.exceptions.ApiException as exc:
        if exc.status == 404:
            warnings.append({"type": "error", "message": f"Secret '{secret_name}' not found in namespace '{namespace}'"})
        else:
            warnings.append({"type": "error", "message": f"Could not read Secret '{secret_name}': {exc.reason}"})
        return warnings

    import base64
    if secret_key not in (secret.data or {}):
        warnings.append({"type": "error", "message": f"Key '{secret_key}' not found in Secret '{secret_name}'"})
        return warnings

    # Parse kubeconfig to check for multiple contexts and token type
    try:
        import yaml as yaml_mod
        raw = base64.b64decode(secret.data[secret_key]).decode("utf-8")
        kc = yaml_mod.safe_load(raw)
        contexts = kc.get("contexts", [])
        if len(contexts) > 1:
            names = [c["name"] for c in contexts]
            warnings.append({
                "type": "warning",
                "message": f"Secret '{secret_name}' contains {len(contexts)} contexts ({', '.join(names)}). "
                           f"oc will use current-context. Recommend a single-context kubeconfig."
            })

        # Check for short-lived OAuth tokens (they look like sha256~... or are very long without dots)
        users = kc.get("users", [])
        for user in users:
            token = (user.get("user") or {}).get("token", "")
            if token and (token.startswith("sha256~") or (len(token) > 100 and "." not in token)):
                warnings.append({
                    "type": "warning",
                    "message": f"User '{user['name']}' appears to use a short-lived OAuth token (expires in 24h). "
                               f"Use a long-lived ServiceAccount token instead. "
                               f"See docs/rhoai-cluster-setup.md for the helper command."
                })
    except Exception as exc:
        warnings.append({"type": "warning", "message": f"Could not parse kubeconfig in Secret '{secret_name}': {type(exc).__name__} — verify the Secret contains valid YAML"})

    return warnings


def set_status(name: str, namespace: str, phase: str, ready: bool, message: str,
               url: str = "", tool_calling_warnings: list | None = None,
               cluster_admin_warnings: list | None = None,
               vllm_config_ready: bool | None = None, **kwargs):
    """Patch the TinycodeInstance status."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conditions = [
        {
            "type": "Ready",
            "status": "True" if ready else "False",
            "reason": "ReconcileSuccess" if ready else "ReconcileError",
            "message": message,
            "lastTransitionTime": now,
        }
    ]
    if tool_calling_warnings:
        conditions.append({
            "type": "VllmWarning",
            "status": "True",
            "reason": "VllmNotFullyConfigured",
            "message": "; ".join(w["message"] for w in tool_calling_warnings),
            "lastTransitionTime": now,
        })
    else:
        conditions.append({
            "type": "VllmWarning",
            "status": "False",
            "reason": "VllmOK",
            "message": "All vLLM services are properly configured",
            "lastTransitionTime": now,
        })
    if vllm_config_ready is not None:
        if vllm_config_ready:
            conditions.append({
                "type": "VllmConfigReady",
                "status": "True",
                "reason": "VllmConfigGenerated",
                "message": "vLLM provider config generated successfully",
                "lastTransitionTime": now,
            })
        else:
            conditions.append({
                "type": "VllmConfigReady",
                "status": "False",
                "reason": "VllmConfigFailed",
                "message": "Failed to generate vLLM provider config",
                "lastTransitionTime": now,
            })
    if cluster_admin_warnings is not None:
        errors = [w for w in cluster_admin_warnings if w.get("type") == "error"]
        if errors:
            conditions.append({
                "type": "ClusterAdminReady",
                "status": "False",
                "reason": "ClusterAdminConfigError",
                "message": "; ".join(w["message"] for w in errors),
                "lastTransitionTime": now,
            })
        else:
            ca_message = "Cluster-admin mode configured successfully"
            if cluster_admin_warnings:
                ca_message += ". Warnings: " + "; ".join(w["message"] for w in cluster_admin_warnings)
            conditions.append({
                "type": "ClusterAdminReady",
                "status": "True",
                "reason": "ClusterAdminReady",
                "message": ca_message,
                "lastTransitionTime": now,
            })

    status_body = {
        "status": {
            "phase": phase,
            "url": url,
            "conditions": conditions,
            "observedGeneration": kwargs.get("body", {}).get("metadata", {}).get("generation", 0),
        }
    }
    try:
        custom_api.patch_namespaced_custom_object_status(
            group=GROUP,
            version=VERSION,
            namespace=namespace,
            plural=PLURAL,
            name=name,
            body=status_body,
        )
    except Exception as exc:
        log.warning("Failed to update status for %s/%s: %s", namespace, name, exc)


def get_route_url(name: str, namespace: str) -> str:
    """Return the external URL of the tinycode Route, if it exists."""
    try:
        dyn_client = get_dynamic_client()
        route_api = dyn_client.resources.get(
            api_version="route.openshift.io/v1", kind="Route"
        )
        route = route_api.get(
            name=f"{name}-tinycode",
            namespace=namespace,
        )
        host = route.spec.host
        tls = route.spec.get("tls")
        scheme = "https" if tls else "http"
        return f"{scheme}://{host}" if host else ""
    except Exception:
        return ""


# ── kopf handlers ─────────────────────────────────────────────────────────────

@kopf.on.create(GROUP, VERSION, PLURAL)
@kopf.on.update(GROUP, VERSION, PLURAL)
async def reconcile(
    name: str,
    namespace: str,
    spec: dict,
    status: dict,
    logger: logging.Logger,
    **kwargs,
):
    """
    Reconcile a TinycodeInstance CR — called on create and update.

    Steps:
    1. Determine the required SCC and bind it to the instance SA.
    2. Run helm upgrade --install with values derived from spec.
    3. Fetch the Route URL and update status.
    """
    logger.info("Reconciling TinycodeInstance %s/%s", namespace, name)
    set_status(name, namespace, "Deploying", False, "Reconciliation in progress", **kwargs)

    # Step 1: Validate git spec
    git_warnings = await asyncio.get_event_loop().run_in_executor(
        None, lambda: validate_git_spec(name, namespace, spec)
    )
    git_errors = [w for w in git_warnings if w.get("type") == "error"]
    if git_errors:
        for w in git_errors:
            logger.error("git validation error: %s", w["message"])
        msg = f"git validation failed: {git_errors[0]['message']}"
        set_status(name, namespace, "Failed", False, msg, **kwargs)
        raise kopf.PermanentError(msg)
    if git_warnings:
        for w in git_warnings:
            logger.warning("git warning: %s", w["message"])

    # Step 2: Validate shared workspace configuration
    workspace_warnings = await asyncio.get_event_loop().run_in_executor(
        None, lambda: validate_shared_workspace(name, namespace, spec)
    )
    workspace_errors = [w for w in workspace_warnings if w.get("type") == "error"]
    if workspace_errors:
        for w in workspace_errors:
            logger.error("shared workspace validation error: %s", w["message"])
        msg = f"shared workspace validation failed: {workspace_errors[0]['message']}"
        set_status(name, namespace, "Failed", False, msg, **kwargs)
        raise kopf.PermanentError(msg)
    if workspace_warnings:
        for w in workspace_warnings:
            logger.warning("shared workspace warning: %s", w["message"])

    # Step 3: Validate clusterAdmin spec (Secret existence, key, contexts, token type)
    ca_warnings = await asyncio.get_event_loop().run_in_executor(
        None, lambda: validate_cluster_admin(name, namespace, spec)
    )
    ca_errors = [w for w in ca_warnings if w.get("type") == "error"]
    if ca_errors:
        for w in ca_errors:
            logger.error("clusterAdmin validation error: %s", w["message"])
        msg = f"clusterAdmin validation failed: {ca_errors[0]['message']}"
        set_status(name, namespace, "Failed", False, msg, cluster_admin_warnings=ca_warnings, **kwargs)
        raise kopf.PermanentError(msg)
    if ca_warnings:
        for w in ca_warnings:
            logger.warning("clusterAdmin warning: %s", w["message"])

    # Step 2: SCC binding
    scc = scc_name_for_spec(spec)
    logger.info("Using SCC: %s", scc)
    try:
        ensure_scc_binding(name, namespace, scc)
    except Exception as exc:
        msg = f"Failed to bind SCC {scc}: {exc}"
        logger.error(msg)
        set_status(name, namespace, "Failed", False, msg, **kwargs)
        raise kopf.PermanentError(msg) from exc

    # Step 3: Build vLLM config and Helm values
    vllm_config_ready = None
    vllm_entries = spec.get("vllm", [])
    if vllm_entries:
        vllm_config = build_vllm_config(spec, namespace)
        vllm_config_ready = vllm_config is not None

    values = helm_values_for_spec(name, namespace, spec)
    release = helm_release_name(name, namespace)

    # Compute hash of Helm values to skip no-op upgrades
    values_hash = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:16]

    # Check if spec has changed
    try:
        cr = custom_api.get_namespaced_custom_object(
            group=GROUP, version=VERSION, namespace=namespace, plural=PLURAL, name=name
        )
        current_hash = cr.get("metadata", {}).get("annotations", {}).get("tinycode.dev/values-hash", "")
        if current_hash == values_hash:
            logger.info("Spec unchanged (hash=%s), skipping helm upgrade", values_hash)
            # Still update status in case route or other external state changed
            url = get_route_url(name, namespace)
            tc_warnings = await asyncio.get_event_loop().run_in_executor(
                None, lambda: check_vllm_tool_calling(namespace)
            )
            msg = f"TinycodeInstance {name} unchanged"
            if url:
                msg += f". URL: {url}"
            ca_warnings_for_status = ca_warnings if spec.get("clusterAdmin", {}).get("enabled", False) else None
            set_status(name, namespace, "Running", True, msg, url=url,
                       tool_calling_warnings=tc_warnings, cluster_admin_warnings=ca_warnings_for_status,
                       vllm_config_ready=vllm_config_ready, **kwargs)
            return
    except Exception:
        pass  # First reconcile or annotation missing — proceed with upgrade

    helm_args = ["upgrade", "--install"]

    ok, output = await asyncio.get_event_loop().run_in_executor(
        None, lambda: run_helm(helm_args, values, release, namespace)
    )
    if not ok:
        msg = f"Helm failed: {output[:500]}"
        logger.error(msg)
        set_status(name, namespace, "Failed", False, msg, vllm_config_ready=vllm_config_ready, **kwargs)
        raise kopf.TemporaryError(msg, delay=60)

    # Step 4: Check vLLM tool calling in the namespace and any configured URLs
    tc_warnings = await asyncio.get_event_loop().run_in_executor(
        None, lambda: check_vllm_tool_calling(namespace)
    )
    if tc_warnings:
        for w in tc_warnings:
            logger.warning("Tool calling not configured: %s", w["message"])

    # Step 5: Fetch Route URL and update status
    url = get_route_url(name, namespace)
    msg = f"TinycodeInstance {name} deployed successfully"
    if url:
        msg += f". URL: {url}"
    if tc_warnings:
        msg += ". WARNING: vLLM tool calling not configured — see status.conditions for details"
    # Pass ca_warnings only when clusterAdmin is enabled (None = no condition appended)
    ca_warnings_for_status = ca_warnings if spec.get("clusterAdmin", {}).get("enabled", False) else None
    set_status(name, namespace, "Running", True, msg, url=url,
               tool_calling_warnings=tc_warnings, cluster_admin_warnings=ca_warnings_for_status,
               vllm_config_ready=vllm_config_ready, **kwargs)

    # Update values hash annotation after successful reconcile
    try:
        custom_api.patch_namespaced_custom_object(
            group=GROUP,
            version=VERSION,
            namespace=namespace,
            plural=PLURAL,
            name=name,
            body={"metadata": {"annotations": {"tinycode.dev/values-hash": values_hash}}},
        )
    except Exception as exc:
        logger.warning("Failed to update values-hash annotation: %s", exc)

    logger.info("Reconcile complete: %s", msg)


@kopf.on.delete(GROUP, VERSION, PLURAL)
async def delete_instance(
    name: str,
    namespace: str,
    logger: logging.Logger,
    **kwargs,
):
    """Clean up helm release and SCC binding when a TinycodeInstance is deleted."""
    logger.info("Deleting TinycodeInstance %s/%s", namespace, name)

    release = helm_release_name(name, namespace)
    if helm_release_exists(release, namespace):
        result = subprocess.run(
            ["helm", "uninstall", release, "--namespace", namespace, "--wait"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            logger.warning("helm uninstall failed: %s", result.stderr)
        else:
            logger.info("Helm release %s uninstalled", release)

    remove_scc_binding(name, namespace)
    logger.info("Deletion complete for %s/%s", namespace, name)


@kopf.on.startup()
async def startup(logger: logging.Logger, **kwargs):
    logger.info("tinycode-operator started. Helm chart: %s", HELM_CHART_PATH)
    # Verify helm is available
    result = subprocess.run(["helm", "version", "--short"], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError("helm not found in PATH — operator cannot function")
    logger.info("helm version: %s", result.stdout.strip())
