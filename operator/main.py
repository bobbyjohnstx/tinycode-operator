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


# ── Helpers ───────────────────────────────────────────────────────────────────

def scc_name_for_spec(spec: dict) -> str:
    """Return the least-privilege SCC name for this instance spec."""
    if spec.get("shell", {}).get("enabled", False):
        return SCC_SHELL
    if spec.get("storage", {}).get("hostPath", {}).get("enabled", False):
        return SCC_HOSTPATH
    return SCC_RESTRICTED


def helm_values_for_spec(name: str, namespace: str, spec: dict) -> dict:
    """Build Helm values from a TinycodeInstanceSpec."""
    storage = spec.get("storage", {})
    host_path = storage.get("hostPath", {})

    values: dict[str, Any] = {
        "instanceName": name,
        "instanceNamespace": namespace,
        "image": spec.get("image", "quay.io/tinycode/server:latest"),
        "replicas": spec.get("replicas", 1),
        "resources": spec.get("resources", {
            "limits": {"cpu": "2", "memory": "2Gi"},
            "requests": {"cpu": "200m", "memory": "512Mi"},
        }),
        "storage": {
            "dataSize": storage.get("dataSize", "1Gi"),
            "projectsSize": storage.get("projectsSize", "10Gi"),
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

    dyn_client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
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
        dyn_client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
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
            models_url = f"http://{cluster_ip}:{port}/v1/models"
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


def set_status(name: str, namespace: str, phase: str, ready: bool, message: str,
               url: str = "", tool_calling_warnings: list | None = None):
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

    status_body = {
        "status": {
            "phase": phase,
            "url": url,
            "conditions": conditions,
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
        dyn_client = kubernetes.dynamic.DynamicClient(
            kubernetes.client.ApiClient()
        )
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
    set_status(name, namespace, "Deploying", False, "Reconciliation in progress")

    # Step 1: SCC binding
    scc = scc_name_for_spec(spec)
    logger.info("Using SCC: %s", scc)
    try:
        ensure_scc_binding(name, namespace, scc)
    except Exception as exc:
        msg = f"Failed to bind SCC {scc}: {exc}"
        logger.error(msg)
        set_status(name, namespace, "Failed", False, msg)
        raise kopf.PermanentError(msg) from exc

    # Step 2: Helm install/upgrade
    values = helm_values_for_spec(name, namespace, spec)
    release = helm_release_name(name, namespace)
    helm_args = ["upgrade", "--install"]

    ok, output = await asyncio.get_event_loop().run_in_executor(
        None, lambda: run_helm(helm_args, values, release, namespace)
    )
    if not ok:
        msg = f"Helm failed: {output[:500]}"
        logger.error(msg)
        set_status(name, namespace, "Failed", False, msg)
        raise kopf.TemporaryError(msg, delay=60)

    # Step 3: Check vLLM tool calling in the namespace and any configured URLs
    tc_warnings = await asyncio.get_event_loop().run_in_executor(
        None, lambda: check_vllm_tool_calling(namespace)
    )
    if tc_warnings:
        for w in tc_warnings:
            logger.warning("Tool calling not configured: %s", w["message"])

    # Step 4: Fetch Route URL and update status
    url = get_route_url(name, namespace)
    msg = f"TinycodeInstance {name} deployed successfully"
    if url:
        msg += f". URL: {url}"
    if tc_warnings:
        msg += ". WARNING: vLLM tool calling not configured — see status.conditions for details"
    set_status(name, namespace, "Running", True, msg, url=url, tool_calling_warnings=tc_warnings)
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
