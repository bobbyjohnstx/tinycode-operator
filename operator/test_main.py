#!/usr/bin/env python3
"""
Unit tests for tinycode-operator pure functions and validation logic.

Tests cover:
- scc_name_for_spec: SCC selection logic
- validate_vllm_url: URL validation and security checks
- validate_shared_workspace: Multi-replica storage validation
- validate_git_spec: Git configuration validation
- helm_values_for_spec: Helm values generation
- helm_release_name: Release name formatting
- build_vllm_config: vLLM provider config generation
"""

import sys
import os
from unittest.mock import MagicMock, patch

# Mock kopf and kubernetes before importing main
sys.modules['kopf'] = MagicMock()
sys.modules['kubernetes'] = MagicMock()
sys.modules['kubernetes.client'] = MagicMock()
sys.modules['kubernetes.config'] = MagicMock()
sys.modules['kubernetes.dynamic'] = MagicMock()
sys.modules['kubernetes.client.exceptions'] = MagicMock()

# Import functions to test
sys.path.insert(0, os.path.dirname(__file__))
from main import (
    scc_name_for_spec,
    validate_vllm_url,
    helm_values_for_spec,
    build_vllm_config,
    validate_shared_workspace,
    validate_git_spec,
    helm_release_name,
)


# ── scc_name_for_spec tests ──────────────────────────────────────────────────

def test_scc_default():
    """Default spec should use restricted SCC."""
    assert scc_name_for_spec({}) == "tinycode-restricted"


def test_scc_shell():
    """Shell enabled should use shell SCC."""
    assert scc_name_for_spec({"shell": {"enabled": True}}) == "tinycode-shell"


def test_scc_hostpath():
    """HostPath configured should use hostpath SCC."""
    assert scc_name_for_spec({"storage": {"hostPath": {"path": "/data"}}}) == "tinycode-hostpath"


def test_scc_shell_priority_over_hostpath():
    """Shell SCC takes priority over hostpath when both are configured."""
    spec = {"shell": {"enabled": True}, "storage": {"hostPath": {"path": "/data"}}}
    assert scc_name_for_spec(spec) == "tinycode-shell"


def test_scc_hostpath_regression_no_enabled_field():
    """Regression test: CRD has path, not enabled field. This bug was fixed."""
    spec = {"storage": {"hostPath": {"path": "/mnt/data", "readOnly": True}}}
    assert scc_name_for_spec(spec) == "tinycode-hostpath"


def test_scc_shell_disabled_explicit():
    """Shell explicitly disabled should not trigger shell SCC."""
    spec = {"shell": {"enabled": False}}
    assert scc_name_for_spec(spec) == "tinycode-restricted"


def test_scc_empty_hostpath():
    """Empty hostPath path should use restricted SCC."""
    spec = {"storage": {"hostPath": {"path": ""}}}
    assert scc_name_for_spec(spec) == "tinycode-restricted"


# ── validate_vllm_url tests ───────────────────────────────────────────────────

def test_valid_url():
    """Valid cluster-local URL should pass."""
    assert validate_vllm_url("http://vllm-svc.ns.svc.cluster.local:8080") == True


def test_metadata_blocked():
    """AWS metadata endpoint should be blocked."""
    assert validate_vllm_url("http://169.254.169.254/latest/meta-data") == False


def test_gcp_metadata_blocked():
    """GCP metadata endpoint should be blocked."""
    assert validate_vllm_url("http://metadata.google.internal/computeMetadata/v1/") == False


def test_loopback_blocked():
    """Loopback IP should be blocked."""
    assert validate_vllm_url("http://127.0.0.1:8080") == False


def test_localhost_allowed():
    """Localhost hostname is allowed (validation only checks IP addresses)."""
    # Note: The function only validates IPs, not hostnames that resolve to loopback
    assert validate_vllm_url("http://localhost:8080") == True


def test_empty_url():
    """Empty URL should be rejected."""
    assert validate_vllm_url("") == False


def test_private_range_allowed():
    """Private IP ranges should be allowed (common in k8s)."""
    assert validate_vllm_url("http://10.0.1.5:8080") == True
    assert validate_vllm_url("http://172.16.0.1:8000") == True
    assert validate_vllm_url("http://192.168.1.100:8080") == True


def test_link_local_blocked():
    """Link-local addresses should be blocked."""
    assert validate_vllm_url("http://169.254.1.1:8080") == False


def test_alicloud_metadata_blocked():
    """Alibaba Cloud metadata endpoint should be blocked."""
    assert validate_vllm_url("http://100.100.100.200/latest/meta-data/") == False


# ── validate_shared_workspace tests ───────────────────────────────────────────

def test_single_replica_rwo_ok():
    """Single replica with RWO should pass."""
    result = validate_shared_workspace("test", "ns", {"replicas": 1})
    assert len(result) == 0


def test_multi_replica_rwo_fails():
    """Multiple replicas with RWO should fail."""
    result = validate_shared_workspace("test", "ns", {"replicas": 3})
    assert len(result) > 0
    assert result[0]["type"] == "error"
    assert "ReadWriteMany" in result[0]["message"]


def test_multi_replica_rwx_ok():
    """Multiple replicas with RWX should pass."""
    result = validate_shared_workspace("test", "ns", {
        "replicas": 3,
        "storage": {"projectsAccessMode": "ReadWriteMany"}
    })
    assert len(result) == 0


def test_multi_replica_hostpath_ok():
    """Multiple replicas with hostPath should pass."""
    result = validate_shared_workspace("test", "ns", {
        "replicas": 3,
        "storage": {"hostPath": {"path": "/data"}}
    })
    assert len(result) == 0


def test_default_replicas():
    """Default replicas (1) should pass with RWO."""
    result = validate_shared_workspace("test", "ns", {})
    assert len(result) == 0


def test_two_replicas_rwo_fails():
    """Two replicas with RWO should fail."""
    result = validate_shared_workspace("test", "ns", {"replicas": 2})
    assert len(result) > 0
    assert "replicas=2" in result[0]["message"]


# ── helm_values_for_spec tests ────────────────────────────────────────────────

def test_minimal_spec():
    """Minimal spec should produce valid default values."""
    values = helm_values_for_spec("test", "ns", {})
    assert values["instanceName"] == "test"
    assert values["instanceNamespace"] == "ns"
    assert values["replicas"] == 1
    assert "quay.io" in values["image"]
    assert values["storage"]["dataSize"] == "1Gi"
    assert values["storage"]["projectsSize"] == "10Gi"
    assert values["storage"]["projectsAccessMode"] == "ReadWriteOnce"


def test_git_spec():
    """Git configuration should be mapped correctly."""
    spec = {"git": {"url": "https://github.com/org/repo.git", "branch": "main"}}
    values = helm_values_for_spec("test", "ns", spec)
    assert values["git"]["enabled"] == True
    assert values["git"]["url"] == "https://github.com/org/repo.git"
    assert values["git"]["branch"] == "main"


def test_git_disabled():
    """No git URL should disable git."""
    values = helm_values_for_spec("test", "ns", {})
    assert values["git"]["enabled"] == False


def test_hostpath_spec():
    """HostPath configuration should be mapped correctly."""
    spec = {"storage": {"hostPath": {"path": "/data", "readOnly": True}}}
    values = helm_values_for_spec("test", "ns", spec)
    assert values["storage"]["hostPath"]["enabled"] == True
    assert values["storage"]["hostPath"]["path"] == "/data"
    assert values["storage"]["hostPath"]["readOnly"] == True


def test_custom_image():
    """Custom image should override default."""
    spec = {"image": "custom.io/tinycode:v1.0"}
    values = helm_values_for_spec("test", "ns", spec)
    assert values["image"] == "custom.io/tinycode:v1.0"


def test_resources_spec():
    """Custom resources should be mapped correctly."""
    spec = {
        "resources": {
            "limits": {"cpu": "4", "memory": "8Gi"},
            "requests": {"cpu": "500m", "memory": "1Gi"}
        }
    }
    values = helm_values_for_spec("test", "ns", spec)
    assert values["resources"]["limits"]["cpu"] == "4"
    assert values["resources"]["requests"]["memory"] == "1Gi"


def test_shell_enabled():
    """Shell enabled should be mapped correctly."""
    spec = {"shell": {"enabled": True}}
    values = helm_values_for_spec("test", "ns", spec)
    assert values["shell"]["enabled"] == True


def test_ollama_config():
    """Ollama configuration should be mapped correctly."""
    spec = {
        "ollama": {
            "enabled": True,
            "host": "http://ollama-svc:11434",
            "models": ["codellama", "llama2"]
        }
    }
    values = helm_values_for_spec("test", "ns", spec)
    assert values["ollama"]["enabled"] == True
    assert values["ollama"]["host"] == "http://ollama-svc:11434"
    assert "codellama" in values["ollama"]["models"]


def test_cluster_admin_config():
    """ClusterAdmin configuration should be mapped correctly."""
    spec = {
        "clusterAdmin": {
            "enabled": True,
            "kubeconfigSecretName": "my-kubeconfig",
            "kubeconfigSecretKey": "config",
            "ocVersion": "4.14"
        }
    }
    values = helm_values_for_spec("test", "ns", spec)
    assert values["clusterAdmin"]["enabled"] == True
    assert values["clusterAdmin"]["kubeconfigSecretName"] == "my-kubeconfig"
    assert values["clusterAdmin"]["kubeconfigSecretKey"] == "config"
    assert values["clusterAdmin"]["ocVersion"] == "4.14"


# ── helm_release_name tests ───────────────────────────────────────────────────

def test_release_name():
    """Release name should include namespace and instance name."""
    result = helm_release_name("my-instance", "my-namespace")
    assert isinstance(result, str)
    assert "my-instance" in result
    assert "my-namespace" in result
    assert result == "tinycode-my-namespace-my-instance"


def test_release_name_special_chars():
    """Release name should handle special characters."""
    result = helm_release_name("test-123", "ns-prod")
    assert result == "tinycode-ns-prod-test-123"


# ── validate_git_spec tests (partial - skip credentialsSecret which needs K8s) ─

def test_no_git_ok():
    """No git configuration should pass."""
    result = validate_git_spec("test", "ns", {})
    assert len(result) == 0


def test_git_and_hostpath_conflict():
    """Git and hostPath should be mutually exclusive."""
    spec = {
        "git": {"url": "https://github.com/org/repo.git"},
        "storage": {"hostPath": {"path": "/data"}}
    }
    result = validate_git_spec("test", "ns", spec)
    assert len(result) > 0
    assert result[0]["type"] == "error"
    assert "mutually exclusive" in result[0]["message"]


def test_git_without_hostpath_ok():
    """Git without hostPath should pass (excluding secret validation)."""
    spec = {"git": {"url": "https://github.com/org/repo.git", "branch": "main"}}
    # This will pass the mutual exclusivity check, but may warn about credentials
    result = validate_git_spec("test", "ns", spec)
    # Filter to only error-level issues
    errors = [w for w in result if w.get("type") == "error"]
    assert len(errors) == 0


# ── build_vllm_config tests ───────────────────────────────────────────────────

def test_empty_vllm():
    """Empty vllm spec should return None."""
    result = build_vllm_config({}, "ns")
    assert result is None


def test_no_vllm_key():
    """Spec without vllm key should return None."""
    result = build_vllm_config({"replicas": 1}, "ns")
    assert result is None


@patch('main.probe_vllm_models')
def test_vllm_with_explicit_limits(mock_probe):
    """vLLM with explicit model limits should use those limits."""
    mock_probe.return_value = []  # No probed models
    spec = {
        "vllm": [{
            "name": "vllm-test",
            "url": "http://vllm:8080",
            "models": {
                "test-model": {
                    "contextLimit": 16000,
                    "outputLimit": 2000
                }
            }
        }]
    }
    result = build_vllm_config(spec, "ns")
    assert result is not None
    assert "providers" in result
    assert "vllm-test" in result["providers"]
    assert result["providers"]["vllm-test"]["type"] == "openai-compatible"
    assert result["providers"]["vllm-test"]["url"] == "http://vllm:8080/v1"
    assert "test-model" in result["providers"]["vllm-test"]["models"]
    assert result["providers"]["vllm-test"]["models"]["test-model"]["contextLimit"] == 16000
    assert result["providers"]["vllm-test"]["models"]["test-model"]["outputLimit"] == 2000


@patch('main.probe_vllm_models')
def test_vllm_with_probed_models(mock_probe):
    """vLLM should auto-detect limits from probed models."""
    mock_probe.return_value = [
        {"id": "llama-3-8b", "max_model_len": 8192}
    ]
    spec = {
        "vllm": [{
            "name": "vllm-local",
            "url": "http://vllm:8080",
            "models": {}
        }]
    }
    result = build_vllm_config(spec, "ns")
    assert result is not None
    assert "llama-3-8b" in result["providers"]["vllm-local"]["models"]
    # 80% of 8192 = 6553.6 -> floor = 6553
    assert result["providers"]["vllm-local"]["models"]["llama-3-8b"]["contextLimit"] == 6553
    # min(4096, 20% of 8192) = min(4096, 1638.4) = 1638
    assert result["providers"]["vllm-local"]["models"]["llama-3-8b"]["outputLimit"] == 1638


@patch('main.probe_vllm_models')
def test_vllm_default_model_config(mock_probe):
    """vLLM spec with default model should include it in config."""
    mock_probe.return_value = []
    spec = {
        "model": "vllm-local/test-model",
        "vllm": [{
            "name": "vllm-local",
            "url": "http://vllm:8080",
            "models": {
                "test-model": {
                    "contextLimit": 8192,
                    "outputLimit": 4096
                }
            }
        }]
    }
    result = build_vllm_config(spec, "ns")
    assert result is not None
    assert result["model"] == "vllm-local/test-model"


@patch('main.probe_vllm_models')
def test_vllm_url_normalization(mock_probe):
    """vLLM URLs should be normalized with /v1 suffix."""
    mock_probe.return_value = []
    spec = {
        "vllm": [{
            "name": "vllm-test",
            "url": "http://vllm:8080/",
            "models": {
                "test": {
                    "contextLimit": 8192,
                    "outputLimit": 4096
                }
            }
        }]
    }
    result = build_vllm_config(spec, "ns")
    assert result["providers"]["vllm-test"]["url"] == "http://vllm:8080/v1"


@patch('main.probe_vllm_models')
def test_vllm_multiple_providers(mock_probe):
    """Multiple vLLM providers should all be included."""
    mock_probe.return_value = []
    spec = {
        "vllm": [
            {
                "name": "vllm-a",
                "url": "http://vllm-a:8080",
                "models": {"model-a": {"contextLimit": 8192, "outputLimit": 4096}}
            },
            {
                "name": "vllm-b",
                "url": "http://vllm-b:8080",
                "models": {"model-b": {"contextLimit": 16384, "outputLimit": 4096}}
            }
        ]
    }
    result = build_vllm_config(spec, "ns")
    assert "vllm-a" in result["providers"]
    assert "vllm-b" in result["providers"]
    assert "model-a" in result["providers"]["vllm-a"]["models"]
    assert "model-b" in result["providers"]["vllm-b"]["models"]


@patch('main.probe_vllm_models')
def test_vllm_user_override_probed_limits(mock_probe):
    """User-specified limits should override probed limits."""
    mock_probe.return_value = [
        {"id": "llama-3-8b", "max_model_len": 8192}
    ]
    spec = {
        "vllm": [{
            "name": "vllm-local",
            "url": "http://vllm:8080",
            "models": {
                "llama-3-8b": {
                    "contextLimit": 4000,
                    "outputLimit": 1000
                }
            }
        }]
    }
    result = build_vllm_config(spec, "ns")
    # User override should win
    assert result["providers"]["vllm-local"]["models"]["llama-3-8b"]["contextLimit"] == 4000
    assert result["providers"]["vllm-local"]["models"]["llama-3-8b"]["outputLimit"] == 1000


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
