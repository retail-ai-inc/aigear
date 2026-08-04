from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from aigear.infrastructure.gcp.image_admission_bootstrap import (
    GkeImageAdmissionBootstrap,
    ImageAdmissionBootstrapError,
)
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.image_admission import ImageAdmissionPolicy


_MODULE = "aigear.infrastructure.gcp.image_admission_bootstrap"


def _bootstrap(context=None):
    policy = ImageAdmissionPolicy(
        schema_version="2.0",
        policy_id="production-images-v1",
        environment_fingerprint=TypedId.from_bare("aa" * 32),
        project_id="prod",
        cluster_specifier="asia-east1.prod-cluster",
        required_attestors=(
            "projects/prod/attestors/security-qualified",
            "projects/prod/attestors/trusted-builder",
        ),
        break_glass_key_versions=("kms/break-glass/versions/1",),
        max_break_glass_ttl_seconds=900,
    )
    return GkeImageAdmissionBootstrap(
        policy,
        cluster_name="prod-cluster",
        location="asia-east1",
        kubectl_context=context,
    )


def test_render_is_versioned_idempotent_and_conflict_detecting(tmp_path):
    bootstrap = _bootstrap()
    policy_path, guard_path = bootstrap.render(tmp_path)
    assert bootstrap.render(tmp_path) == (policy_path, guard_path)

    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    guard = json.loads(guard_path.read_text(encoding="utf-8"))
    assert policy["defaultAdmissionRule"]["evaluationMode"] == "REQUIRE_ATTESTATION"
    assert guard["items"][0]["spec"]["failurePolicy"] == "Fail"
    assert guard["items"][1]["spec"]["validationActions"] == ["Deny", "Audit"]
    assert "image-policy.k8s.io/break-glass" in json.dumps(guard)
    assert "initContainers" in json.dumps(guard)
    assert "ephemeralContainers" in json.dumps(guard)

    policy_path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ImageAdmissionBootstrapError, match="different content"):
        bootstrap.render(tmp_path)


@patch(f"{_MODULE}.run_sh")
def test_enable_cluster_uses_current_enforcement_mode(mock_run_sh):
    _bootstrap().enable_cluster_enforcement()

    command = mock_run_sh.call_args.args[0]
    assert command[:5] == ["gcloud", "container", "clusters", "update", "prod-cluster"]
    assert "--location=asia-east1" in command
    assert "--binauthz-evaluation-mode=PROJECT_SINGLETON_POLICY_ENFORCE" in command
    assert mock_run_sh.call_args.kwargs == {"check": True, "timeout": 600}


@patch(f"{_MODULE}.run_sh")
def test_apply_imports_strict_policy_then_server_side_guard(mock_run_sh, tmp_path):
    _bootstrap(context="gke_prod").apply(tmp_path)

    policy_command = mock_run_sh.call_args_list[2].args[0]
    guard_command = mock_run_sh.call_args_list[3].args[0]
    assert policy_command[:5] == [
        "gcloud",
        "container",
        "binauthz",
        "policy",
        "import",
    ]
    assert "--strict-validation" in policy_command
    assert guard_command[:3] == ["kubectl", "--context=gke_prod", "apply"]
    assert "--server-side" in guard_command

    attestor_commands = [call.args[0] for call in mock_run_sh.call_args_list[:2]]
    assert all(command[:5] == [
        "gcloud", "container", "binauthz", "attestors", "describe"
    ] for command in attestor_commands)


@patch(f"{_MODULE}.run_sh")
def test_enable_apis_includes_binary_authorization_and_container_analysis(mock_run_sh):
    _bootstrap().enable_apis()

    command = mock_run_sh.call_args.args[0]
    assert "binaryauthorization.googleapis.com" in command
    assert "containeranalysis.googleapis.com" in command
    assert mock_run_sh.call_args.kwargs == {"check": True}
