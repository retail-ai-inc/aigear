from __future__ import annotations

import pytest

from aigear.management.v2.control_document import (
    ControlDocument,
    InvalidControlDocumentError,
    parse_schema_version,
)
from aigear.management.v2.environment import RegistryBinding, generate_registry_binding_id
from aigear.management.v2.identifiers import TypedId

_FINGERPRINT_HEX = "cd" * 32


# ── parse_schema_version ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [("2.0", (2, 0)), ("0.1", (0, 1)), ("10.23", (10, 23))],
)
def test_parse_schema_version_accepts_canonical_form(value, expected):
    assert parse_schema_version(value) == expected


@pytest.mark.parametrize(
    "value",
    ["2", "2.0.1", "02.0", "2.00", "2.", ".0", "2,0", "", "v2.0", 2.0],
)
def test_parse_schema_version_rejects_non_canonical_or_wrong_type(value):
    with pytest.raises(InvalidControlDocumentError):
        parse_schema_version(value)


# ── ControlDocument ──────────────────────────────────────────────────────────────


def _fingerprint() -> TypedId:
    return TypedId.from_bare(_FINGERPRINT_HEX)


def _binding(fingerprint: TypedId | None = None) -> RegistryBinding:
    return RegistryBinding(
        firestore_database_id="(default)",
        registry_binding_id=generate_registry_binding_id(),
        registry_binding_epoch=1,
        bound_environment_fingerprint=fingerprint or _fingerprint(),
    )


def _control_document(**overrides) -> ControlDocument:
    defaults = dict(
        schema_version="2.0",
        environment_id="production",
        authority="v1",
        phase="v1_only",
        write_epoch=1,
        min_reader_version="2.0",
        min_writer_version="2.0",
        required_capabilities=("typed_gcs_layout_v2", "projection_fencing_v2"),
        environment_fingerprint=_fingerprint(),
        registry_binding=_binding(),
        registry_bound_by="deployer@aigear",
    )
    defaults.update(overrides)
    return ControlDocument(**defaults)


def test_control_document_accepts_well_formed_fields():
    doc = _control_document()
    assert doc.authority == "v1"
    assert doc.required_capabilities == ("typed_gcs_layout_v2", "projection_fencing_v2")


def test_control_document_normalizes_list_capabilities_to_tuple():
    doc = _control_document(required_capabilities=["typed_gcs_layout_v2"])
    assert doc.required_capabilities == ("typed_gcs_layout_v2",)


@pytest.mark.parametrize("field_name", ["schema_version", "min_reader_version", "min_writer_version"])
def test_control_document_rejects_malformed_schema_version_fields(field_name):
    with pytest.raises(InvalidControlDocumentError):
        _control_document(**{field_name: "2"})


def test_control_document_rejects_unknown_authority():
    with pytest.raises(InvalidControlDocumentError):
        _control_document(authority="v3")


@pytest.mark.parametrize("epoch", [0, -1, "1", 1.0, True])
def test_control_document_rejects_invalid_write_epoch(epoch):
    with pytest.raises(InvalidControlDocumentError):
        _control_document(write_epoch=epoch)


def test_control_document_rejects_empty_required_capabilities():
    with pytest.raises(InvalidControlDocumentError):
        _control_document(required_capabilities=())


def test_control_document_rejects_blank_capability_string():
    with pytest.raises(InvalidControlDocumentError):
        _control_document(required_capabilities=("ok", ""))


def test_control_document_rejects_non_typed_id_fingerprint():
    with pytest.raises(InvalidControlDocumentError):
        _control_document(environment_fingerprint=_FINGERPRINT_HEX)  # type: ignore[arg-type]


def test_control_document_rejects_non_registry_binding():
    with pytest.raises(InvalidControlDocumentError):
        _control_document(registry_binding=object())  # type: ignore[arg-type]


def test_control_document_rejects_fingerprint_mismatch_with_binding():
    other_fingerprint = TypedId.from_bare("ef" * 32)
    with pytest.raises(InvalidControlDocumentError):
        _control_document(
            environment_fingerprint=other_fingerprint,
            registry_binding=_binding(fingerprint=_fingerprint()),
        )


def test_control_document_rejects_empty_registry_bound_by():
    with pytest.raises(InvalidControlDocumentError):
        _control_document(registry_bound_by="")


def test_control_document_optional_fields_default_to_none():
    doc = _control_document()
    assert doc.registry_rebind_operation_id is None
    assert doc.v1_compat_projection_enabled_until is None
    assert doc.old_writer_fenced_at is None
