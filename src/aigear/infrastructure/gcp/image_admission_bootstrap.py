"""Versioned GKE Binary Authorization and native break-glass guard bootstrap."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from aigear.common import run_sh
from aigear.management.v2.image_admission import ImageAdmissionPolicy

__all__ = ["GkeImageAdmissionBootstrap", "ImageAdmissionBootstrapError"]


class ImageAdmissionBootstrapError(ValueError):
    pass


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


@dataclass(frozen=True)
class GkeImageAdmissionBootstrap:
    policy: ImageAdmissionPolicy
    cluster_name: str
    location: str
    kubectl_context: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.policy, ImageAdmissionPolicy):
            raise ImageAdmissionBootstrapError("policy must be an ImageAdmissionPolicy")
        for field in ("cluster_name", "location"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value:
                raise ImageAdmissionBootstrapError(f"{field} must be non-empty")

    @property
    def native_break_glass_guard(self) -> dict:
        policy_name = "deny-native-binauthz-break-glass.aigear.openai.com"
        return {
            "apiVersion": "v1",
            "kind": "List",
            "items": [
                {
                    "apiVersion": "admissionregistration.k8s.io/v1",
                    "kind": "ValidatingAdmissionPolicy",
                    "metadata": {
                        "name": policy_name,
                        "annotations": {
                            "aigear.openai.com/policy-id": self.policy.policy_id,
                            "aigear.openai.com/schema-version": self.policy.schema_version,
                        },
                    },
                    "spec": {
                        "failurePolicy": "Fail",
                        "matchConstraints": {
                            "resourceRules": [
                                {
                                    "apiGroups": [""],
                                    "apiVersions": ["v1"],
                                    "operations": ["CREATE", "UPDATE"],
                                    "resources": ["pods"],
                                }
                            ]
                        },
                        "validations": [
                            {
                                "expression": (
                                    "object.spec.containers.all(c, "
                                    "c.image.matches('^.+@sha256:[0-9a-f]{64}$')) && "
                                    "(!has(object.spec.initContainers) || "
                                    "object.spec.initContainers.all(c, "
                                    "c.image.matches('^.+@sha256:[0-9a-f]{64}$'))) && "
                                    "(!has(object.spec.ephemeralContainers) || "
                                    "object.spec.ephemeralContainers.all(c, "
                                    "c.image.matches('^.+@sha256:[0-9a-f]{64}$')))"
                                ),
                                "message": "every container image must be digest pinned",
                                "reason": "Forbidden",
                            },
                            {
                                "expression": (
                                    "!has(object.metadata.labels) || "
                                    "!('image-policy.k8s.io/break-glass' "
                                    "in object.metadata.labels)"
                                ),
                                "message": (
                                    "native Binary Authorization break-glass is disabled; "
                                    "use a signed, expiring Aigear grant"
                                ),
                                "reason": "Forbidden",
                            }
                        ],
                        "auditAnnotations": [
                            {
                                "key": "native-break-glass-attempt",
                                "valueExpression": (
                                    "has(object.metadata.labels) && "
                                    "'image-policy.k8s.io/break-glass' in "
                                    "object.metadata.labels ? request.userInfo.username : null"
                                ),
                            }
                        ],
                    },
                },
                {
                    "apiVersion": "admissionregistration.k8s.io/v1",
                    "kind": "ValidatingAdmissionPolicyBinding",
                    "metadata": {"name": policy_name},
                    "spec": {
                        "policyName": policy_name,
                        "validationActions": ["Deny", "Audit"],
                    },
                },
            ],
        }

    def render(self, directory: str | Path) -> tuple[Path, Path]:
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        policy_path = target / f"binary-authorization-{self.policy.policy_id}.json"
        guard_path = target / f"native-break-glass-guard-{self.policy.policy_id}.json"
        self._write_versioned(policy_path, self.policy.to_binary_authorization_policy())
        self._write_versioned(guard_path, self.native_break_glass_guard)
        return policy_path, guard_path

    @staticmethod
    def _write_versioned(path: Path, value: object) -> None:
        content = _json(value)
        if path.exists():
            if path.read_text(encoding="utf-8") != content:
                raise ImageAdmissionBootstrapError(
                    "versioned admission configuration already exists with different content"
                )
            return
        path.write_text(content, encoding="utf-8")

    def enable_apis(self) -> None:
        run_sh(
            [
                "gcloud",
                "services",
                "enable",
                "binaryauthorization.googleapis.com",
                "containeranalysis.googleapis.com",
                f"--project={self.policy.project_id}",
            ],
            check=True,
        )

    def enable_cluster_enforcement(self) -> None:
        run_sh(
            [
                "gcloud",
                "container",
                "clusters",
                "update",
                self.cluster_name,
                f"--location={self.location}",
                "--binauthz-evaluation-mode=PROJECT_SINGLETON_POLICY_ENFORCE",
                f"--project={self.policy.project_id}",
                "--quiet",
            ],
            check=True,
            timeout=600,
        )

    def verify_attestors(self) -> None:
        for resource in self.policy.required_attestors:
            run_sh(
                [
                    "gcloud",
                    "container",
                    "binauthz",
                    "attestors",
                    "describe",
                    resource.rsplit("/", 1)[-1],
                    f"--project={self.policy.project_id}",
                ],
                check=True,
            )

    def apply(self, directory: str | Path) -> None:
        policy_path, guard_path = self.render(directory)
        self.verify_attestors()
        run_sh(
            [
                "gcloud",
                "container",
                "binauthz",
                "policy",
                "import",
                str(policy_path),
                "--strict-validation",
                f"--project={self.policy.project_id}",
                "--quiet",
            ],
            check=True,
        )
        command = ["kubectl"]
        if self.kubectl_context is not None:
            command.append(f"--context={self.kubectl_context}")
        command.extend(
            [
                "apply",
                "--server-side",
                "--field-manager=aigear-security-bootstrap",
                f"--filename={guard_path}",
            ]
        )
        run_sh(command, check=True)
