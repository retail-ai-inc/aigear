"""Versioned namespace network isolation for release workloads."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

from aigear.common import run_sh
from aigear.management.v2.naming import validate_segment

__all__ = [
    "NetworkDependency",
    "ReleaseNetworkPolicy",
    "RuntimeNetworkPolicyError",
]


class RuntimeNetworkPolicyError(ValueError):
    pass


def _labels(field_name: str, value: object) -> Tuple[Tuple[str, str], ...]:
    if isinstance(value, list):
        value = tuple(value)
    if (
        not isinstance(value, tuple)
        or not value
        or not all(
            isinstance(item, tuple)
            and len(item) == 2
            and all(isinstance(part, str) and part for part in item)
            for item in value
        )
        or value != tuple(sorted(value))
        or len({key for key, _value in value}) != len(value)
    ):
        raise RuntimeNetworkPolicyError(
            f"{field_name} must be non-empty, sorted, and unique"
        )
    return value


def _port(field_name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise RuntimeNetworkPolicyError(f"{field_name} must be a valid TCP port")
    return value


@dataclass(frozen=True)
class NetworkDependency:
    name: str
    namespace: str
    pod_labels: Tuple[Tuple[str, str], ...]
    port: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", validate_segment(self.name, field_name="name"))
        object.__setattr__(
            self,
            "namespace",
            validate_segment(self.namespace, field_name="namespace"),
        )
        object.__setattr__(self, "pod_labels", _labels("pod_labels", self.pod_labels))
        _port("port", self.port)

    def to_egress_rule(self) -> dict:
        return {
            "to": [
                {
                    "namespaceSelector": {
                        "matchLabels": {
                            "kubernetes.io/metadata.name": self.namespace
                        }
                    },
                    "podSelector": {"matchLabels": dict(self.pod_labels)},
                }
            ],
            "ports": [{"protocol": "TCP", "port": self.port}],
        }


@dataclass(frozen=True)
class ReleaseNetworkPolicy:
    policy_id: str
    namespace: str
    ingress_namespace: str
    ingress_pod_labels: Tuple[Tuple[str, str], ...]
    workload_port: int
    dependencies: Tuple[NetworkDependency, ...]
    kubectl_context: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("policy_id", "namespace", "ingress_namespace"):
            object.__setattr__(
                self,
                field_name,
                validate_segment(getattr(self, field_name), field_name=field_name),
            )
        object.__setattr__(
            self,
            "ingress_pod_labels",
            _labels("ingress_pod_labels", self.ingress_pod_labels),
        )
        _port("workload_port", self.workload_port)
        if isinstance(self.dependencies, list):
            object.__setattr__(self, "dependencies", tuple(self.dependencies))
        if not all(isinstance(value, NetworkDependency) for value in self.dependencies):
            raise RuntimeNetworkPolicyError(
                "dependencies must contain NetworkDependency values"
            )
        names = tuple(value.name for value in self.dependencies)
        if names != tuple(sorted(names)) or len(set(names)) != len(names):
            raise RuntimeNetworkPolicyError(
                "dependencies must be sorted and unique by name"
            )

    @property
    def manifest(self) -> dict:
        metadata = {
            "namespace": self.namespace,
            "annotations": {"aigear.openai.com/policy-id": self.policy_id},
        }
        default_deny = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {**metadata, "name": "aigear-default-deny"},
            "spec": {
                "podSelector": {},
                "policyTypes": ["Ingress", "Egress"],
            },
        }
        release_allow = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {**metadata, "name": "aigear-release-allow"},
            "spec": {
                "podSelector": {
                    "matchExpressions": [
                        {
                            "key": "aigear.openai.com/release-id",
                            "operator": "Exists",
                        }
                    ]
                },
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [
                    {
                        "from": [
                            {
                                "namespaceSelector": {
                                    "matchLabels": {
                                        "kubernetes.io/metadata.name": (
                                            self.ingress_namespace
                                        )
                                    }
                                },
                                "podSelector": {
                                    "matchLabels": dict(self.ingress_pod_labels)
                                },
                            }
                        ],
                        "ports": [
                            {"protocol": "TCP", "port": self.workload_port}
                        ],
                    }
                ],
                "egress": [
                    {
                        "to": [
                            {
                                "namespaceSelector": {
                                    "matchLabels": {
                                        "kubernetes.io/metadata.name": "kube-system"
                                    }
                                },
                                "podSelector": {
                                    "matchLabels": {"k8s-app": "kube-dns"}
                                },
                            }
                        ],
                        "ports": [
                            {"protocol": "UDP", "port": 53},
                            {"protocol": "TCP", "port": 53},
                        ],
                    },
                    *(value.to_egress_rule() for value in self.dependencies),
                ],
            },
        }
        return {"apiVersion": "v1", "kind": "List", "items": [default_deny, release_allow]}

    def render(self, directory: str | Path) -> Path:
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"runtime-network-policy-{self.policy_id}.json"
        content = json.dumps(
            self.manifest, ensure_ascii=False, indent=2, sort_keys=True
        ) + "\n"
        if path.exists():
            if path.read_text(encoding="utf-8") != content:
                raise RuntimeNetworkPolicyError(
                    "versioned network policy already exists with different content"
                )
            return path
        path.write_text(content, encoding="utf-8")
        return path

    def apply(self, directory: str | Path) -> None:
        path = self.render(directory)
        command = ["kubectl"]
        if self.kubectl_context is not None:
            command.append(f"--context={self.kubectl_context}")
        command.extend(
            [
                "apply",
                "--server-side",
                "--field-manager=aigear-security-bootstrap",
                f"--filename={path}",
            ]
        )
        run_sh(command, check=True)
