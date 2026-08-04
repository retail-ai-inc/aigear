from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from aigear.infrastructure.gcp.runtime_network_policy import (
    NetworkDependency,
    ReleaseNetworkPolicy,
    RuntimeNetworkPolicyError,
)


_MODULE = "aigear.infrastructure.gcp.runtime_network_policy"


def _policy(context=None):
    return ReleaseNetworkPolicy(
        policy_id="production-runtime-v1",
        namespace="aigear",
        ingress_namespace="gateway-system",
        ingress_pod_labels=(("app.kubernetes.io/name", "gateway"),),
        workload_port=8080,
        dependencies=(
            NetworkDependency(
                name="runtime-authorizer",
                namespace="aigear-security",
                pod_labels=(("app.kubernetes.io/name", "runtime-authorizer"),),
                port=8443,
            ),
        ),
        kubectl_context=context,
    )


def test_manifest_defaults_namespace_to_deny_and_only_allows_declared_flows():
    manifest = _policy().manifest
    default_deny, release_allow = manifest["items"]

    assert default_deny["metadata"]["namespace"] == "aigear"
    assert default_deny["spec"] == {
        "podSelector": {},
        "policyTypes": ["Ingress", "Egress"],
    }
    assert release_allow["spec"]["podSelector"]["matchExpressions"][0][
        "key"
    ] == "aigear.openai.com/release-id"
    assert release_allow["spec"]["ingress"][0]["from"][0][
        "namespaceSelector"
    ]["matchLabels"] == {"kubernetes.io/metadata.name": "gateway-system"}
    assert release_allow["spec"]["ingress"][0]["ports"] == [
        {"protocol": "TCP", "port": 8080}
    ]
    assert release_allow["spec"]["egress"][0]["ports"] == [
        {"protocol": "UDP", "port": 53},
        {"protocol": "TCP", "port": 53},
    ]
    assert release_allow["spec"]["egress"][1] == (
        _policy().dependencies[0].to_egress_rule()
    )
    assert "0.0.0.0/0" not in json.dumps(manifest)


def test_dependencies_must_be_explicit_sorted_and_unique():
    dependency = _policy().dependencies[0]
    with pytest.raises(RuntimeNetworkPolicyError, match="sorted and unique"):
        ReleaseNetworkPolicy(
            policy_id="production-runtime-v1",
            namespace="aigear",
            ingress_namespace="gateway-system",
            ingress_pod_labels=(("app", "gateway"),),
            workload_port=8080,
            dependencies=(dependency, dependency),
        )


def test_render_is_versioned_idempotent_and_conflict_detecting(tmp_path):
    policy = _policy()
    path = policy.render(tmp_path)
    assert policy.render(tmp_path) == path

    path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(RuntimeNetworkPolicyError, match="different content"):
        policy.render(tmp_path)


@patch(f"{_MODULE}.run_sh")
def test_apply_uses_server_side_field_ownership(mock_run_sh, tmp_path):
    _policy(context="gke_prod").apply(tmp_path)

    command = mock_run_sh.call_args.args[0]
    assert command[:3] == ["kubectl", "--context=gke_prod", "apply"]
    assert "--server-side" in command
    assert "--field-manager=aigear-security-bootstrap" in command
    assert mock_run_sh.call_args.kwargs == {"check": True}
