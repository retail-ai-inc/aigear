"""The single typed GCS path builder/parser for Pipeline V2 (spec sections 6.2/6.3).

``GcsLayoutV2`` is the *only* place allowed to construct or parse a V2-managed
GCS object name. Finalizer, projection consumer, resolver, migrator, GC,
reconciler, CLI and ``LocalGCSMock`` must all go through it instead of
hand-rolling string concatenation, so every caller agrees on the exact
directory layout, byte limits and root-escape defenses.

This module intentionally covers only the layout kinds required to start
Phase A / the finalize-and-resolve read path (canonical blob, staging,
quarantine, asset/run readable projections). Export/promotion path builders
(``manual_export_from_asset``, ``manual_export_from_run``, ``promotion_export``)
are deferred to the task that implements the export operation flow, to avoid
adding untested, unused surface area now.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from aigear.management.v2.identifiers import (
    SHA256_TYPED_PREFIX,
    TypedId,
)
from aigear.management.v2.naming import validate_segment

__all__ = [
    "GcsLayoutError",
    "PAYLOAD_KINDS",
    "MAX_OBJECT_NAME_BYTES",
    "ParsedObjectPath",
    "GcsLayoutV2",
]

MAX_OBJECT_NAME_BYTES = 900

# Closed enum per spec section 5.1: "临时/导出路径必须再携带封闭
# payload_kind ∈ {components, attachments}".
PAYLOAD_KINDS = frozenset({"components", "attachments"})

_REGISTRY_SEGMENT = "registry"
_V2_SEGMENT = "v2"


class GcsLayoutError(ValueError):
    """Raised for structural V2 GCS path errors (root escape, bad shape, size)."""


@dataclass(frozen=True)
class ParsedObjectPath:
    """The result of :meth:`GcsLayoutV2.parse_and_validate`.

    ``kind`` identifies which layout branch matched; ``fields`` carries the
    extracted, individually validated dynamic segments for that kind.
    """

    kind: str
    fields: Mapping[str, str]


def _coerce_typed_id(value: "TypedId | str") -> TypedId:
    if isinstance(value, TypedId):
        return value
    if isinstance(value, str) and value.startswith(SHA256_TYPED_PREFIX):
        return TypedId.from_typed(value)
    return TypedId.from_bare(value)


def _validate_attempt_no(attempt_no: int) -> str:
    if isinstance(attempt_no, bool) or not isinstance(attempt_no, int) or attempt_no < 1:
        raise GcsLayoutError(f"attempt_no must be a positive int, got {attempt_no!r}")
    return str(attempt_no)


def _validate_payload_kind(payload_kind: str) -> str:
    if payload_kind not in PAYLOAD_KINDS:
        raise GcsLayoutError(
            f"payload_kind must be one of {sorted(PAYLOAD_KINDS)}, got {payload_kind!r}"
        )
    return payload_kind


@dataclass(frozen=True)
class GcsLayoutV2:
    """Typed path builder/parser rooted at ``<bucket>/<project>/<pipeline>/registry/v2/``."""

    bucket_name: str
    project_name: str
    pipeline_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.bucket_name, str) or not self.bucket_name or "/" in self.bucket_name:
            raise GcsLayoutError(f"bucket_name must be a non-empty, slash-free str, got {self.bucket_name!r}")
        validate_segment(self.project_name, field_name="project_name")
        validate_segment(self.pipeline_version, field_name="pipeline_version")

    # ── root ─────────────────────────────────────────────────────────────

    @property
    def object_prefix(self) -> str:
        """``V2_OBJECT_PREFIX``: no leading/trailing slash."""
        return f"{self.project_name}/{self.pipeline_version}/{_REGISTRY_SEGMENT}/{_V2_SEGMENT}"

    @property
    def root_uri(self) -> str:
        """``GCS_V2_ROOT``: always has exactly one trailing slash."""
        return f"gs://{self.bucket_name}/{self.object_prefix}/"

    def to_uri(self, object_name: str) -> str:
        return f"gs://{self.bucket_name}/{object_name}"

    def _build(self, *parts: str) -> str:
        object_name = "/".join((self.object_prefix, *parts))
        encoded_len = len(object_name.encode("utf-8"))
        if encoded_len > MAX_OBJECT_NAME_BYTES:
            raise GcsLayoutError(
                f"object name exceeds {MAX_OBJECT_NAME_BYTES} UTF-8 bytes "
                f"({encoded_len} bytes): {object_name!r}"
            )
        return object_name

    # ── builders ─────────────────────────────────────────────────────────

    def canonical_blob(self, blob_id: "TypedId | str") -> str:
        typed_id = _coerce_typed_id(blob_id)
        digest = typed_id.bare
        return self._build("_objects", "sha256", digest[:2], digest)

    def staging(
        self,
        *,
        run_id: str,
        step_name: str,
        attempt_no: int,
        operation_id: str,
        output_name: str,
        payload_kind: str,
        payload_key: str,
        file_name: str,
    ) -> str:
        return self._build(
            "_staging",
            validate_segment(run_id, field_name="run_id"),
            validate_segment(step_name, field_name="step_name"),
            _validate_attempt_no(attempt_no),
            validate_segment(operation_id, field_name="operation_id"),
            validate_segment(output_name, field_name="output_name"),
            _validate_payload_kind(payload_kind),
            validate_segment(payload_key, field_name="payload_key"),
            validate_segment(file_name, field_name="file_name"),
        )

    def quarantine(
        self,
        *,
        import_operation_id: str,
        payload_kind: str,
        payload_key: str,
        file_name: str,
    ) -> str:
        return self._build(
            "_quarantine",
            validate_segment(import_operation_id, field_name="import_operation_id"),
            _validate_payload_kind(payload_kind),
            validate_segment(payload_key, field_name="payload_key"),
            validate_segment(file_name, field_name="file_name"),
        )

    def quarantine_manifest(self, import_operation_id: str) -> str:
        return self._build(
            "_quarantine",
            validate_segment(import_operation_id, field_name="import_operation_id"),
            "source-manifest.json",
        )

    def asset_projection(
        self, asset_type: str, asset_name: str, display_version: str
    ) -> str:
        return self._build(
            "assets",
            validate_segment(asset_type, field_name="asset_type"),
            validate_segment(asset_name, field_name="asset_name"),
            "versions",
            validate_segment(display_version, field_name="display_version"),
            "manifest.json",
        )

    def run_projection(self, run_id: str, step_name: str, output_name: str) -> str:
        return self._build(
            "runs",
            validate_segment(run_id, field_name="run_id"),
            "steps",
            validate_segment(step_name, field_name="step_name"),
            "outputs",
            validate_segment(output_name, field_name="output_name"),
            "occurrence.json",
        )

    # ── parser ───────────────────────────────────────────────────────────

    def parse_and_validate(self, uri: str) -> ParsedObjectPath:
        """Parse and fully validate a ``gs://`` URI produced by this layout.

        Verifies scheme, exact bucket match, that the object lives under this
        instance's managed root (no root escape), that there are no empty
        path segments, the 900-byte object name cap, and that every dynamic
        segment independently passes :func:`validate_segment`.
        """
        if not isinstance(uri, str) or not uri.startswith("gs://"):
            raise GcsLayoutError(f"Not a gs:// URI: {uri!r}")

        without_scheme = uri[len("gs://") :]
        bucket, sep, object_name = without_scheme.partition("/")
        if not sep or not object_name:
            raise GcsLayoutError(f"URI is missing an object name: {uri!r}")
        if bucket != self.bucket_name:
            raise GcsLayoutError(
                f"bucket mismatch: expected {self.bucket_name!r}, got {bucket!r}"
            )

        encoded_len = len(object_name.encode("utf-8"))
        if encoded_len > MAX_OBJECT_NAME_BYTES:
            raise GcsLayoutError(
                f"object name exceeds {MAX_OBJECT_NAME_BYTES} UTF-8 bytes "
                f"({encoded_len} bytes): {object_name!r}"
            )

        prefix = self.object_prefix + "/"
        if not object_name.startswith(prefix):
            raise GcsLayoutError(
                f"object name is outside the V2 managed root {prefix!r}: {object_name!r}"
            )
        if "//" in object_name:
            raise GcsLayoutError(f"object name must not contain empty segments: {object_name!r}")

        remainder = object_name[len(prefix) :]
        if not remainder:
            raise GcsLayoutError(f"object name has no path under the V2 root: {object_name!r}")

        segments = remainder.split("/")
        dispatch = {
            "_objects": self._parse_canonical_blob,
            "assets": self._parse_asset_projection,
            "runs": self._parse_run_projection,
            "_staging": self._parse_staging,
            "_quarantine": self._parse_quarantine,
        }
        handler = dispatch.get(segments[0])
        if handler is None:
            raise GcsLayoutError(f"unrecognized V2 object path prefix: {segments[0]!r}")
        try:
            return handler(segments)
        except GcsLayoutError:
            raise
        except ValueError as exc:
            # Normalize per-segment failures (InvalidSegmentError, InvalidTypedIdError)
            # into a single exception type at the parse boundary.
            raise GcsLayoutError(str(exc)) from exc

    # ── parser branches ──────────────────────────────────────────────────

    def _parse_canonical_blob(self, segments: list) -> ParsedObjectPath:
        if len(segments) != 4 or segments[1] != "sha256":
            raise GcsLayoutError(f"malformed canonical blob path: {segments!r}")
        shard, digest = segments[2], segments[3]
        typed_id = TypedId.from_bare(digest)
        if shard != typed_id.bare[:2]:
            raise GcsLayoutError(
                f"blob shard {shard!r} does not match digest prefix {typed_id.bare[:2]!r}"
            )
        return ParsedObjectPath(kind="canonical_blob", fields={"blob_id": typed_id.typed})

    def _parse_asset_projection(self, segments: list) -> ParsedObjectPath:
        if len(segments) != 6 or segments[3] != "versions" or segments[5] != "manifest.json":
            raise GcsLayoutError(f"malformed asset projection path: {segments!r}")
        asset_type = validate_segment(segments[1], field_name="asset_type")
        asset_name = validate_segment(segments[2], field_name="asset_name")
        display_version = validate_segment(segments[4], field_name="display_version")
        return ParsedObjectPath(
            kind="asset_projection",
            fields={
                "asset_type": asset_type,
                "asset_name": asset_name,
                "display_version": display_version,
            },
        )

    def _parse_run_projection(self, segments: list) -> ParsedObjectPath:
        if (
            len(segments) != 7
            or segments[2] != "steps"
            or segments[4] != "outputs"
            or segments[6] != "occurrence.json"
        ):
            raise GcsLayoutError(f"malformed run projection path: {segments!r}")
        run_id = validate_segment(segments[1], field_name="run_id")
        step_name = validate_segment(segments[3], field_name="step_name")
        output_name = validate_segment(segments[5], field_name="output_name")
        return ParsedObjectPath(
            kind="run_projection",
            fields={"run_id": run_id, "step_name": step_name, "output_name": output_name},
        )

    def _parse_staging(self, segments: list) -> ParsedObjectPath:
        if len(segments) != 9:
            raise GcsLayoutError(f"malformed staging path: {segments!r}")
        (
            _marker,
            run_id,
            step_name,
            attempt_no,
            operation_id,
            output_name,
            payload_kind,
            payload_key,
            file_name,
        ) = segments
        if not attempt_no.isdigit() or attempt_no != str(int(attempt_no)) or int(attempt_no) < 1:
            raise GcsLayoutError(f"malformed attempt_no in staging path: {attempt_no!r}")
        return ParsedObjectPath(
            kind="staging",
            fields={
                "run_id": validate_segment(run_id, field_name="run_id"),
                "step_name": validate_segment(step_name, field_name="step_name"),
                "attempt_no": attempt_no,
                "operation_id": validate_segment(operation_id, field_name="operation_id"),
                "output_name": validate_segment(output_name, field_name="output_name"),
                "payload_kind": _validate_payload_kind(payload_kind),
                "payload_key": validate_segment(payload_key, field_name="payload_key"),
                "file_name": validate_segment(file_name, field_name="file_name"),
            },
        )

    def _parse_quarantine(self, segments: list) -> ParsedObjectPath:
        if len(segments) == 3 and segments[2] == "source-manifest.json":
            import_operation_id = validate_segment(segments[1], field_name="import_operation_id")
            return ParsedObjectPath(
                kind="quarantine_manifest",
                fields={"import_operation_id": import_operation_id},
            )
        if len(segments) == 5:
            _marker, import_operation_id, payload_kind, payload_key, file_name = segments
            return ParsedObjectPath(
                kind="quarantine",
                fields={
                    "import_operation_id": validate_segment(
                        import_operation_id, field_name="import_operation_id"
                    ),
                    "payload_kind": _validate_payload_kind(payload_kind),
                    "payload_key": validate_segment(payload_key, field_name="payload_key"),
                    "file_name": validate_segment(file_name, field_name="file_name"),
                },
            )
        raise GcsLayoutError(f"malformed quarantine path: {segments!r}")
