from __future__ import annotations

from dataclasses import replace

import pytest

from aigear.management.v2.attestation import HmacTestVerifier
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.asset_version import TrustState
from aigear.management.v2.runtime_binding import (
    RuntimeBindingConflict,
    VerifiedJournalWatermark,
    compute_runtime_binding_digest,
    resolve_runtime_assets,
    revalidate_current_runtime_bindings,
)
from tests.management.v2.test_release_prepare import _inputs
from tests.management.v2.test_resolver import _NOW


def _binding_inputs():
    registry, control, layout, asset, _runtime, manifest = _inputs()
    journal = VerifiedJournalWatermark(
        security_watermark=0,
        head_entry_id=TypedId.from_bare("fa" * 32),
        verified_at=_NOW,
        fresh_for_seconds=60,
    )
    handles = resolve_runtime_assets(
        registry,
        control=control,
        layout=layout,
        manifest=manifest,
        at=_NOW,
        verifier=HmacTestVerifier(),
    )
    return registry, asset, manifest, journal, handles


def test_runtime_binding_seals_release_assets_policy_location_and_journal():
    _registry, _asset, manifest, journal, handles = _binding_inputs()
    first = compute_runtime_binding_digest(manifest, handles, journal)

    assert first != compute_runtime_binding_digest(
        manifest,
        handles,
        replace(journal, head_entry_id=TypedId.from_bare("fb" * 32)),
    )
    assert first != compute_runtime_binding_digest(
        manifest, handles, replace(journal, security_watermark=1)
    )


def test_runtime_binding_revalidation_rejects_immediate_revocation():
    registry, asset, _manifest, _journal, handles = _binding_inputs()
    revalidate_current_runtime_bindings(registry, handles=handles, at=_NOW)
    registry.put_asset_version(replace(asset, trust_state=TrustState.REVOKED))

    with pytest.raises(RuntimeBindingConflict):
        revalidate_current_runtime_bindings(registry, handles=handles, at=_NOW)


def test_journal_watermark_rejects_naive_time_and_unbounded_freshness():
    with pytest.raises(ValueError, match="timezone-aware"):
        VerifiedJournalWatermark(
            security_watermark=0,
            head_entry_id=TypedId.from_bare("fa" * 32),
            verified_at=_NOW.replace(tzinfo=None),
            fresh_for_seconds=60,
        )
    with pytest.raises(ValueError, match="between 1 and 900"):
        VerifiedJournalWatermark(
            security_watermark=0,
            head_entry_id=TypedId.from_bare("fa" * 32),
            verified_at=_NOW,
            fresh_for_seconds=901,
        )
