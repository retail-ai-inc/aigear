"""Bounded, point-in-time evidence closure for policy decisions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Mapping, Optional, Tuple

from aigear.management.v2.attestation import AttestationRecord
from aigear.management.v2.canonical import canonicalize_json
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.record_codec import encode_record
from aigear.management.v2.records.asset_version import (
    AssetVersionRecord,
    LifecycleState,
    ProducerSpec,
    TrustState,
)
from aigear.management.v2.records.blob import AvailabilityState, BlobRecord
from aigear.management.v2.records.import_operation import (
    ImportProvenanceIndexRecord,
)
from aigear.management.v2.records.lineage import (
    LineageEdge,
    compute_component_edge_id,
    compute_lineage_edge_id,
)
from aigear.management.v2.records.occurrence import (
    OccurrenceRecord,
    OccurrenceStatus,
)

__all__ = [
    "PolicyEvidenceError",
    "EvidenceNodeKind",
    "PolicyEvidenceNode",
    "PolicyEvidenceLink",
    "PolicyEvidenceRequest",
    "PolicyEvidenceClosure",
    "compute_producer_identity",
    "compute_policy_evidence_closure",
]


class PolicyEvidenceError(ValueError):
    pass


class EvidenceNodeKind(str, Enum):
    ASSET_VERSION = "asset_version"
    PRODUCER = "producer"
    MANIFEST_ATTESTATION = "manifest_attestation"
    COMPONENT_EDGE = "component_edge"
    BLOB = "blob"
    BLOB_LOCATION_REVISION = "blob_location_revision"
    BLOB_LOCATION_ATTESTATION = "blob_location_attestation"
    IMPORT_PROVENANCE_INDEX = "import_provenance_index"
    SOURCE_PROVENANCE_ATTESTATION = "source_provenance_attestation"
    OCCURRENCE = "occurrence"
    OCCURRENCE_ATTESTATION = "occurrence_attestation"
    LINEAGE_EDGE = "lineage_edge"
    INPUT_OCCURRENCE = "input_occurrence"


def _aware_utc(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise PolicyEvidenceError("read_time must be timezone-aware")
    return value.astimezone(timezone.utc)


def _digest(value: object) -> TypedId:
    return TypedId.from_bare(hashlib.sha256(canonicalize_json(value)).hexdigest())


def compute_producer_identity(producer: ProducerSpec) -> TypedId:
    if not isinstance(producer, ProducerSpec):
        raise PolicyEvidenceError("producer must be a ProducerSpec")
    return _digest(
        {
            "domain": "aigear.producer-identity.v2",
            **producer.to_manifest_dict(),
        }
    )


@dataclass(frozen=True)
class PolicyEvidenceNode:
    kind: EvidenceNodeKind
    identity: str
    evidence_digest: TypedId
    evidence: Mapping[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, EvidenceNodeKind):
            raise PolicyEvidenceError("node kind must be EvidenceNodeKind")
        if not isinstance(self.identity, str) or not self.identity:
            raise PolicyEvidenceError("node identity must be non-empty")
        if not isinstance(self.evidence_digest, TypedId):
            raise PolicyEvidenceError("node evidence_digest must be TypedId")
        if not isinstance(self.evidence, Mapping):
            raise PolicyEvidenceError("node evidence must be a mapping")
        if self.evidence_digest != _digest(
            [
                "aigear.policy-evidence-node.v2",
                self.kind.value,
                self.identity,
                dict(self.evidence),
            ]
        ):
            raise PolicyEvidenceError("node evidence_digest does not match evidence")

    @property
    def node_id(self) -> str:
        return f"{self.kind.value}:{self.identity}"

    def canonical_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "identity": self.identity,
            "evidence_digest": self.evidence_digest.typed,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True, order=True)
class PolicyEvidenceLink:
    source_node_id: str
    relation: str
    target_node_id: str

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value
            for value in (
                self.source_node_id,
                self.relation,
                self.target_node_id,
            )
        ):
            raise PolicyEvidenceError("evidence link fields must be non-empty")

    def canonical_dict(self) -> dict:
        return {
            "source_node_id": self.source_node_id,
            "relation": self.relation,
            "target_node_id": self.target_node_id,
        }


@dataclass(frozen=True)
class PolicyEvidenceRequest:
    subject_asset_version_id: TypedId
    environment_fingerprint: TypedId
    read_time: datetime
    allowed_producer_identities: Tuple[TypedId, ...]
    allowed_scanners: Tuple[Tuple[str, str], ...]
    max_depth: int = 8
    max_nodes: int = 500
    page_size: int = 50

    def __post_init__(self) -> None:
        if isinstance(self.allowed_producer_identities, list):
            object.__setattr__(
                self,
                "allowed_producer_identities",
                tuple(self.allowed_producer_identities),
            )
        if isinstance(self.allowed_scanners, list):
            object.__setattr__(self, "allowed_scanners", tuple(self.allowed_scanners))
        if not isinstance(self.subject_asset_version_id, TypedId):
            raise PolicyEvidenceError("subject_asset_version_id must be TypedId")
        if not isinstance(self.environment_fingerprint, TypedId):
            raise PolicyEvidenceError("environment_fingerprint must be TypedId")
        object.__setattr__(self, "read_time", _aware_utc(self.read_time))
        if not self.allowed_producer_identities or not all(
            isinstance(value, TypedId) for value in self.allowed_producer_identities
        ):
            raise PolicyEvidenceError(
                "allowed_producer_identities must contain TypedId values"
            )
        producer_values = [value.typed for value in self.allowed_producer_identities]
        if producer_values != sorted(producer_values) or len(set(producer_values)) != len(
            producer_values
        ):
            raise PolicyEvidenceError(
                "allowed_producer_identities must be unique and sorted"
            )
        if not self.allowed_scanners or not all(
            isinstance(value, tuple)
            and len(value) == 2
            and all(isinstance(part, str) and part for part in value)
            for value in self.allowed_scanners
        ):
            raise PolicyEvidenceError(
                "allowed_scanners must contain (scanner_id, version) tuples"
            )
        if list(self.allowed_scanners) != sorted(self.allowed_scanners) or len(
            set(self.allowed_scanners)
        ) != len(self.allowed_scanners):
            raise PolicyEvidenceError("allowed_scanners must be unique and sorted")
        if (
            isinstance(self.max_depth, bool)
            or not isinstance(self.max_depth, int)
            or not 0 <= self.max_depth <= 32
        ):
            raise PolicyEvidenceError("max_depth must be between 0 and 32")
        if (
            isinstance(self.max_nodes, bool)
            or not isinstance(self.max_nodes, int)
            or not 1 <= self.max_nodes <= 10_000
        ):
            raise PolicyEvidenceError("max_nodes must be between 1 and 10000")
        if (
            isinstance(self.page_size, bool)
            or not isinstance(self.page_size, int)
            or not 1 <= self.page_size <= 500
        ):
            raise PolicyEvidenceError("page_size must be between 1 and 500")

    @property
    def read_time_text(self) -> str:
        return self.read_time.isoformat()

    @property
    def filter_digest(self) -> TypedId:
        return _digest(
            {
                "domain": "aigear.policy-evidence-filter.v2",
                "subject_asset_version_id": self.subject_asset_version_id.typed,
                "environment_fingerprint": self.environment_fingerprint.typed,
                "read_time": self.read_time_text,
                "allowed_producer_identities": [
                    value.typed for value in self.allowed_producer_identities
                ],
                "allowed_scanners": [list(value) for value in self.allowed_scanners],
                "max_depth": self.max_depth,
                "max_nodes": self.max_nodes,
                "page_size": self.page_size,
                "traversal": "asset-inputs-depth-first-v1",
            }
        )


@dataclass(frozen=True)
class PolicyEvidenceClosure:
    subject_asset_version_id: TypedId
    environment_fingerprint: TypedId
    read_time: str
    filter_digest: TypedId
    nodes: Tuple[PolicyEvidenceNode, ...]
    links: Tuple[PolicyEvidenceLink, ...]
    approvable: bool
    rejection_reasons: Tuple[str, ...]
    closure_digest: TypedId

    def __post_init__(self) -> None:
        if tuple(sorted(self.nodes, key=lambda value: value.node_id)) != self.nodes:
            raise PolicyEvidenceError("closure nodes must be deterministically sorted")
        if tuple(sorted(self.links)) != self.links:
            raise PolicyEvidenceError("closure links must be deterministically sorted")
        if tuple(sorted(set(self.rejection_reasons))) != self.rejection_reasons:
            raise PolicyEvidenceError("rejection_reasons must be unique and sorted")
        if self.approvable == bool(self.rejection_reasons):
            raise PolicyEvidenceError(
                "approvable must be true exactly when rejection_reasons is empty"
            )
        if self.closure_digest != _closure_digest(self):
            raise PolicyEvidenceError("closure_digest does not match closure")


def _closure_digest(value: PolicyEvidenceClosure) -> TypedId:
    return _digest(
        {
            "domain": "aigear.policy-evidence-closure.v2",
            "subject_asset_version_id": value.subject_asset_version_id.typed,
            "environment_fingerprint": value.environment_fingerprint.typed,
            "read_time": value.read_time,
            "filter_digest": value.filter_digest.typed,
            "nodes": [item.canonical_dict() for item in value.nodes],
            "links": [item.canonical_dict() for item in value.links],
            "approvable": value.approvable,
            "rejection_reasons": list(value.rejection_reasons),
        }
    )


class _ClosureBuilder:
    def __init__(self, view, request: PolicyEvidenceRequest) -> None:
        self.view = view
        self.request = request
        self.nodes: dict[str, PolicyEvidenceNode] = {}
        self.links: set[PolicyEvidenceLink] = set()
        self.reasons: set[str] = set()
        self.visited_assets: set[TypedId] = set()
        self.exhausted = False

    def reject(self, reason: str) -> None:
        self.reasons.add(reason)

    def add_node(
        self,
        kind: EvidenceNodeKind,
        identity: str,
        value: object,
    ) -> Optional[PolicyEvidenceNode]:
        evidence = encode_record(value)
        if not isinstance(evidence, dict):
            evidence = {"value": evidence}
        digest = _digest(
            ["aigear.policy-evidence-node.v2", kind.value, identity, evidence]
        )
        candidate = PolicyEvidenceNode(kind, identity, digest, evidence)
        existing = self.nodes.get(candidate.node_id)
        if existing is not None:
            if existing != candidate:
                self.reject("evidence_identity_conflict")
            return existing
        if len(self.nodes) >= self.request.max_nodes:
            self.reject("max_nodes_exceeded")
            self.exhausted = True
            return None
        self.nodes[candidate.node_id] = candidate
        return candidate

    def link(
        self,
        source: Optional[PolicyEvidenceNode],
        relation: str,
        target: Optional[PolicyEvidenceNode],
    ) -> None:
        if source is not None and target is not None:
            self.links.add(
                PolicyEvidenceLink(source.node_id, relation, target.node_id)
            )

    def read(self, method_name: str, *args):
        method = getattr(self.view, method_name, None)
        if not callable(method):
            self.reject(f"reader_missing_{method_name}")
            return None
        try:
            return method(*args)
        except Exception:
            self.reject(f"evidence_read_failed_{method_name}")
            return None

    def paged(self, method_name: str, *, asset_version_id: TypedId):
        method = getattr(self.view, method_name, None)
        if not callable(method):
            self.reject(f"reader_missing_{method_name}")
            return ()
        result = []
        cursor = None
        while len(result) < self.request.max_nodes:
            limit = min(
                self.request.page_size,
                self.request.max_nodes - len(result),
            )
            try:
                page = tuple(
                    method(
                        asset_version_id=asset_version_id,
                        cursor=cursor,
                        limit=limit,
                    )
                )
            except Exception:
                self.reject(f"evidence_read_failed_{method_name}")
                return tuple(result)
            if len(page) > limit:
                self.reject(f"reader_unbounded_{method_name}")
                return tuple(result)
            if not page:
                return tuple(result)
            identities = [
                (
                    value.occurrence_id.typed
                    if isinstance(value, OccurrenceRecord)
                    else value.source_provenance_attestation_ref.typed
                )
                for value in page
            ]
            if identities != sorted(identities) or len(set(identities)) != len(
                identities
            ):
                self.reject(f"reader_nondeterministic_{method_name}")
                return tuple(result)
            if cursor is not None and identities[0] <= cursor:
                self.reject(f"reader_cursor_drift_{method_name}")
                return tuple(result)
            result.extend(page)
            cursor = identities[-1]
            if len(page) < limit:
                return tuple(result)
        try:
            overflow = tuple(
                method(
                    asset_version_id=asset_version_id,
                    cursor=cursor,
                    limit=1,
                )
            )
        except Exception:
            self.reject(f"evidence_read_failed_{method_name}")
            return tuple(result)
        if overflow:
            self.reject("max_nodes_exceeded")
        return tuple(result)

    def validate_environment(self, value: object) -> bool:
        actual = getattr(value, "environment_fingerprint", None)
        if actual != self.request.environment_fingerprint:
            self.reject("environment_mismatch")
            return False
        return True

    def add_attestation(
        self,
        *,
        ref: TypedId,
        kind: EvidenceNodeKind,
        expected_kind: str,
        subject_fields: Mapping[str, object],
        source_node: Optional[PolicyEvidenceNode],
        relation: str,
    ) -> Optional[AttestationRecord]:
        value = self.read("get_attestation", ref)
        if not isinstance(value, AttestationRecord):
            self.reject(f"missing_{expected_kind}_attestation")
            return None
        self.validate_environment(value)
        subject = value.unsigned_envelope.get("subject")
        if (
            value.attestation_kind != expected_kind
            or value.attestation_id != ref
            or not isinstance(subject, Mapping)
            or any(subject.get(key) != expected for key, expected in subject_fields.items())
        ):
            self.reject(f"invalid_{expected_kind}_attestation")
        node = self.add_node(kind, value.attestation_id.typed, value)
        self.link(source_node, relation, node)
        return value

    def visit_blob(
        self,
        asset_node: Optional[PolicyEvidenceNode],
        asset: AssetVersionRecord,
        component,
    ) -> None:
        if self.exhausted:
            return
        edge_id = compute_component_edge_id(
            asset.asset_version_id,
            component.role,
            component.logical_name,
            component.blob_id,
        )
        edge = self.read("get_component_edge", edge_id)
        if (
            edge is None
            or edge.asset_version_id != asset.asset_version_id
            or edge.blob_id != component.blob_id
            or edge.role != component.role
            or edge.logical_name != component.logical_name
        ):
            self.reject("missing_or_invalid_component_edge")
            return
        self.validate_environment(edge)
        edge_node = self.add_node(
            EvidenceNodeKind.COMPONENT_EDGE, edge_id.typed, edge
        )
        self.link(asset_node, "component", edge_node)

        blob = self.read("get_blob", component.blob_id)
        if not isinstance(blob, BlobRecord):
            self.reject("missing_component_blob")
            return
        self.validate_environment(blob)
        if blob.availability_state is not AvailabilityState.READY:
            self.reject("component_blob_not_ready")
        blob_node = self.add_node(EvidenceNodeKind.BLOB, blob.blob_id.typed, blob)
        self.link(edge_node, "blob", blob_node)
        revision = self.read(
            "get_blob_location_revision",
            blob.blob_id,
            blob.current_location_revision,
        )
        if (
            revision is None
            or revision.blob_id != blob.blob_id
            or revision.location_revision != blob.current_location_revision
            or revision.location_attestation_ref
            != blob.current_location_attestation_ref
            or revision.location_chain_head != blob.location_chain_head
            or revision.bucket != blob.bucket
            or revision.object_name != blob.object_name
            or revision.generation != blob.generation
        ):
            self.reject("missing_or_invalid_blob_location_revision")
            return
        self.validate_environment(revision)
        revision_node = self.add_node(
            EvidenceNodeKind.BLOB_LOCATION_REVISION,
            f"{blob.blob_id.typed}:{blob.current_location_revision}",
            revision,
        )
        self.link(blob_node, "current_location", revision_node)
        self.add_attestation(
            ref=blob.current_location_attestation_ref,
            kind=EvidenceNodeKind.BLOB_LOCATION_ATTESTATION,
            expected_kind="blob_location",
            subject_fields={
                "blob_id": blob.blob_id.typed,
                "location_revision": blob.current_location_revision,
                "bucket": blob.bucket,
                "object_name": blob.object_name,
                "generation": blob.generation,
            },
            source_node=revision_node,
            relation="location_attestation",
        )

    def validate_embedded_inspection(
        self, subject: Mapping[str, object], asset: AssetVersionRecord
    ) -> None:
        raw = subject.get("inspection_evidence")
        keys = {
            "operation_id",
            "ticket_digest",
            "environment_fingerprint",
            "fencing_token",
            "payload_set_digest",
            "policy_digest",
            "producer_evidence_digest",
            "governance_digest",
            "payloads",
            "inspected_at",
            "evidence_digest",
        }
        if not isinstance(raw, dict) or set(raw) != keys:
            self.reject("missing_scanner_evidence")
            return
        core = {"domain": "aigear.content-inspection.v2"}
        core.update({key: raw[key] for key in keys if key != "evidence_digest"})
        expected_digest = _digest(core).typed
        if (
            raw["evidence_digest"] != expected_digest
            or subject.get("inspection_evidence_digest") != expected_digest
            or raw["environment_fingerprint"]
            != self.request.environment_fingerprint.typed
            or raw["operation_id"] != subject.get("operation_id")
            or raw["ticket_digest"] != subject.get("ticket_digest")
            or raw["producer_evidence_digest"]
            != subject.get("producer_evidence_digest")
            or not isinstance(raw["payloads"], list)
        ):
            self.reject("invalid_scanner_evidence")
            return
        source_payloads = subject.get("payloads")
        if not isinstance(source_payloads, list):
            self.reject("invalid_source_provenance_payloads")
            return
        source_payload_keys = {
            "payload_kind",
            "payload_key",
            "semantic_kind",
            "logical_name",
            "media_type",
            "blob_id",
            "size_bytes",
            "crc32c",
            "source_bucket",
            "source_object_name",
            "source_generation",
            "quarantine_bucket",
            "quarantine_object_name",
            "quarantine_generation",
        }
        scanner_payload_keys = {
            "payload_kind",
            "payload_key",
            "quarantine_object_name",
            "quarantine_generation",
            "sha256",
            "size_bytes",
            "format_name",
            "scanner_id",
            "scanner_version",
            "scanner_verdict",
            "scanner_finding_codes",
            "scanner_completed_at",
            "scanner_report_digest",
        }
        if any(
            not isinstance(value, dict) or set(value) != source_payload_keys
            for value in source_payloads
        ) or any(
            not isinstance(value, dict) or set(value) != scanner_payload_keys
            for value in raw["payloads"]
        ):
            self.reject("invalid_scanner_or_source_payload_evidence")
            return
        source_by_key = {
            (value.get("payload_kind"), value.get("payload_key")): value
            for value in source_payloads
        }
        scan_by_key = {
            (value.get("payload_kind"), value.get("payload_key")): value
            for value in raw["payloads"]
        }
        if (
            len(source_by_key) != len(source_payloads)
            or len(scan_by_key) != len(raw["payloads"])
            or set(source_by_key) != set(scan_by_key)
        ):
            self.reject("scanner_payload_coverage_mismatch")
            return
        component_blobs = {
            value.blob_id.typed for value in asset.components
        }
        provenance_component_blobs = {
            value.get("blob_id")
            for value in source_payloads
            if value.get("payload_kind") == "components"
        }
        if component_blobs != provenance_component_blobs:
            self.reject("source_provenance_component_coverage_mismatch")
        for key in sorted(scan_by_key):
            scanned = scan_by_key[key]
            source = source_by_key[key]
            scanner = (scanned.get("scanner_id"), scanned.get("scanner_version"))
            finding_codes = scanned.get("scanner_finding_codes")
            report_core = {
                "domain": "aigear.content-scan-report.v2",
                "scanner_id": scanned.get("scanner_id"),
                "scanner_version": scanned.get("scanner_version"),
                "payload_sha256": scanned.get("sha256"),
                "verdict": scanned.get("scanner_verdict"),
                "finding_codes": finding_codes,
                "completed_at": scanned.get("scanner_completed_at"),
            }
            try:
                report_digest = _digest(report_core).typed
                scanner_completed = datetime.fromisoformat(
                    scanned.get("scanner_completed_at")
                )
                inspected = datetime.fromisoformat(raw["inspected_at"])
            except (TypeError, ValueError):
                self.reject("untrusted_or_invalid_scanner_evidence")
                continue
            if (
                scanner not in self.request.allowed_scanners
                or scanned.get("scanner_verdict") != "clean"
                or not isinstance(finding_codes, list)
                or not all(
                    isinstance(value, str) and value and len(value) <= 128
                    for value in finding_codes
                )
                or not isinstance(source.get("blob_id"), str)
                or scanned.get("sha256")
                != source["blob_id"].removeprefix("sha256:")
                or scanned.get("quarantine_object_name")
                != source.get("quarantine_object_name")
                or scanned.get("quarantine_generation")
                != source.get("quarantine_generation")
                or scanned.get("size_bytes") != source.get("size_bytes")
                or scanned.get("scanner_report_digest") != report_digest
                or scanner_completed.tzinfo is None
                or scanner_completed.utcoffset() is None
                or inspected.tzinfo is None
                or inspected.utcoffset() is None
                or scanner_completed > inspected
            ):
                self.reject("untrusted_or_invalid_scanner_evidence")

    def visit_import_provenance(
        self,
        asset_node: Optional[PolicyEvidenceNode],
        asset: AssetVersionRecord,
    ) -> int:
        if self.exhausted:
            return 0
        records = self.paged(
            "query_import_provenance_by_asset",
            asset_version_id=asset.asset_version_id,
        )
        for value in records:
            if (
                not isinstance(value, ImportProvenanceIndexRecord)
                or value.asset_version_id != asset.asset_version_id
            ):
                self.reject("invalid_import_provenance_index")
                continue
            self.validate_environment(value)
            node = self.add_node(
                EvidenceNodeKind.IMPORT_PROVENANCE_INDEX,
                value.source_provenance_attestation_ref.typed,
                value,
            )
            self.link(asset_node, "import_provenance", node)
            attestation = self.add_attestation(
                ref=value.source_provenance_attestation_ref,
                kind=EvidenceNodeKind.SOURCE_PROVENANCE_ATTESTATION,
                expected_kind="source_provenance",
                subject_fields={
                    "provenance_kind": "external_import",
                    "asset_version_id": asset.asset_version_id.typed,
                    "operation_id": value.operation_id,
                    "ticket_digest": value.ticket_digest.typed,
                },
                source_node=node,
                relation="source_attestation",
            )
            if attestation is not None:
                self.validate_embedded_inspection(
                    attestation.unsigned_envelope["subject"], asset
                )
        return len(records)

    def visit_occurrence_provenance(
        self,
        asset_node: Optional[PolicyEvidenceNode],
        asset: AssetVersionRecord,
    ) -> int:
        if self.exhausted:
            return 0
        records = self.paged(
            "query_occurrences_by_asset",
            asset_version_id=asset.asset_version_id,
        )
        asset_inputs = {
            value.binding_name: value.asset_version_id
            for value in asset.input_bindings
        }
        for value in records:
            if (
                not isinstance(value, OccurrenceRecord)
                or value.status is not OccurrenceStatus.COMMITTED
                or value.asset_version_id != asset.asset_version_id
            ):
                self.reject("invalid_producer_occurrence")
                continue
            self.validate_environment(value)
            node = self.add_node(
                EvidenceNodeKind.OCCURRENCE, value.occurrence_id.typed, value
            )
            self.link(asset_node, "producer_occurrence", node)
            self.add_attestation(
                ref=value.finalization_attestation_ref,
                kind=EvidenceNodeKind.OCCURRENCE_ATTESTATION,
                expected_kind="occurrence_finalization",
                subject_fields={
                    "producer_kind": "normal_pipeline",
                    "occurrence_id": value.occurrence_id.typed,
                    "asset_version_id": asset.asset_version_id.typed,
                    "resolved_inputs_digest": value.resolved_inputs_digest.typed,
                },
                source_node=node,
                relation="finalization_attestation",
            )
            occurrence_inputs = {
                binding.binding_name: binding.asset_version_id
                for binding in value.resolved_input_bindings
            }
            if occurrence_inputs != asset_inputs:
                self.reject("occurrence_input_manifest_mismatch")
            for binding in value.resolved_input_bindings:
                if binding.occurrence_id is None:
                    continue
                input_occurrence = self.read("get_occurrence", binding.occurrence_id)
                if (
                    not isinstance(input_occurrence, OccurrenceRecord)
                    or input_occurrence.status is not OccurrenceStatus.COMMITTED
                    or input_occurrence.asset_version_id != binding.asset_version_id
                ):
                    self.reject("missing_or_invalid_input_occurrence")
                    continue
                self.validate_environment(input_occurrence)
                input_node = self.add_node(
                    EvidenceNodeKind.INPUT_OCCURRENCE,
                    input_occurrence.occurrence_id.typed,
                    input_occurrence,
                )
                edge_id = compute_lineage_edge_id(
                    value.occurrence_id,
                    binding.binding_name,
                    input_occurrence.occurrence_id,
                )
                edge = self.read("get_lineage_edge", edge_id)
                if (
                    not isinstance(edge, LineageEdge)
                    or edge.output_occurrence_id != value.occurrence_id
                    or edge.input_occurrence_id != input_occurrence.occurrence_id
                    or edge.binding_name != binding.binding_name
                ):
                    self.reject("missing_or_invalid_lineage_edge")
                    continue
                self.validate_environment(edge)
                edge_node = self.add_node(
                    EvidenceNodeKind.LINEAGE_EDGE, edge.edge_id.typed, edge
                )
                self.link(node, "lineage", edge_node)
                self.link(edge_node, "input_occurrence", input_node)
        return len(records)

    def visit_asset(
        self,
        asset_version_id: TypedId,
        *,
        depth: int,
        ancestors: frozenset[TypedId],
    ) -> None:
        if self.exhausted:
            return
        if asset_version_id in ancestors:
            self.reject("asset_input_cycle")
            return
        if depth > self.request.max_depth:
            self.reject("max_depth_exceeded")
            return
        if asset_version_id in self.visited_assets:
            return
        asset = self.read("get_asset_version", asset_version_id)
        if not isinstance(asset, AssetVersionRecord):
            self.reject("missing_asset_version")
            return
        self.visited_assets.add(asset_version_id)
        self.validate_environment(asset)
        if asset.lifecycle_state is not LifecycleState.ACTIVE:
            self.reject("asset_not_active")
        if asset.trust_state is TrustState.REVOKED:
            self.reject("asset_revoked")
        asset_node = self.add_node(
            EvidenceNodeKind.ASSET_VERSION, asset.asset_version_id.typed, asset
        )
        producer_identity = compute_producer_identity(asset.producer_spec)
        producer_node = self.add_node(
            EvidenceNodeKind.PRODUCER,
            producer_identity.typed,
            {
                "producer_identity": producer_identity.typed,
                **asset.producer_spec.to_manifest_dict(),
            },
        )
        self.link(asset_node, "producer", producer_node)
        if producer_identity not in self.request.allowed_producer_identities:
            self.reject("unknown_producer")
        self.add_attestation(
            ref=asset.manifest_integrity_attestation_ref,
            kind=EvidenceNodeKind.MANIFEST_ATTESTATION,
            expected_kind="asset_manifest_integrity",
            subject_fields={
                "asset_version_id": asset.asset_version_id.typed,
                "manifest_digest": asset.manifest_digest.typed,
            },
            source_node=asset_node,
            relation="manifest_attestation",
        )
        for component in asset.components:
            self.visit_blob(asset_node, asset, component)
            if self.exhausted:
                break
        import_count = self.visit_import_provenance(asset_node, asset)
        occurrence_count = self.visit_occurrence_provenance(asset_node, asset)
        if import_count + occurrence_count == 0:
            self.reject("missing_asset_provenance")
        next_ancestors = ancestors | {asset_version_id}
        for binding in asset.input_bindings:
            self.visit_asset(
                binding.asset_version_id,
                depth=depth + 1,
                ancestors=next_ancestors,
            )
            target = self.nodes.get(
                f"{EvidenceNodeKind.ASSET_VERSION.value}:"
                f"{binding.asset_version_id.typed}"
            )
            self.link(asset_node, f"input:{binding.binding_name}", target)


def _empty_or_complete_closure(
    request: PolicyEvidenceRequest,
    builder: _ClosureBuilder,
) -> PolicyEvidenceClosure:
    nodes = tuple(sorted(builder.nodes.values(), key=lambda value: value.node_id))
    links = tuple(sorted(builder.links))
    reasons = tuple(sorted(builder.reasons))
    values = {
        "subject_asset_version_id": request.subject_asset_version_id,
        "environment_fingerprint": request.environment_fingerprint,
        "read_time": request.read_time_text,
        "filter_digest": request.filter_digest,
        "nodes": nodes,
        "links": links,
        "approvable": not reasons,
        "rejection_reasons": reasons,
    }
    provisional = PolicyEvidenceClosure.__new__(PolicyEvidenceClosure)
    for name, value in values.items():
        object.__setattr__(provisional, name, value)
    return PolicyEvidenceClosure(
        **values,
        closure_digest=_closure_digest(provisional),
    )


def compute_policy_evidence_closure(
    registry,
    request: PolicyEvidenceRequest,
) -> PolicyEvidenceClosure:
    """Read a deterministic closure from one fixed Registry snapshot."""

    if not isinstance(request, PolicyEvidenceRequest):
        raise PolicyEvidenceError("request must be a PolicyEvidenceRequest")
    factory = getattr(registry, "at_read_time", None)
    if not callable(factory):
        builder = _ClosureBuilder(registry, request)
        builder.reject("fixed_read_time_unavailable")
        return _empty_or_complete_closure(request, builder)
    try:
        view = factory(request.read_time)
    except Exception:
        builder = _ClosureBuilder(registry, request)
        builder.reject("fixed_read_time_unavailable")
        return _empty_or_complete_closure(request, builder)
    actual_read_time = getattr(view, "read_time", None)
    try:
        normalized_read_time = _aware_utc(actual_read_time)
    except PolicyEvidenceError:
        normalized_read_time = None
    if normalized_read_time != request.read_time:
        builder = _ClosureBuilder(view, request)
        builder.reject("read_time_drift")
        return _empty_or_complete_closure(request, builder)
    builder = _ClosureBuilder(view, request)
    builder.visit_asset(
        request.subject_asset_version_id,
        depth=0,
        ancestors=frozenset(),
    )
    return _empty_or_complete_closure(request, builder)
