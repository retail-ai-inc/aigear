# Migrating from `VersionedAssetManagement` to `PipelineAssetManagement`

> Status: Phase A (see `docs/pipeline-v2-phase-a-tasks.md`, task T11). This
> guide describes the *current* state of the migration, which is
> intentionally incomplete. Do not use `PipelineAssetManagement` for
> production workloads yet — see [What is not implemented yet](#what-is-not-implemented-yet).

## Why a new class?

`docs/pipeline-asset-lifecycle-management-v2.md` (section 24.1) introduces
`PipelineAssetManagement` as the eventual replacement for
`VersionedAssetManagement`. It is built directly on the V2 data model
(Blob / AssetVersion / Occurrence, spec section 3) instead of the legacy
flat `AssetRecord`, so it can express exact-content references, resolved
input provenance, and per-attempt execution state that
`VersionedAssetManagement` cannot represent.

`VersionedAssetManagement` is not being removed or changed by this
migration. It keeps its v0.2.0 behavior (pinned by the T1 snapshot tests)
for the entire compatibility period described in the spec.

## What is implemented today

`PipelineAssetManagement` currently wires up:

- An `EnvironmentIdentity` (spec 2.1) and its derived `environment_fingerprint`,
  computed once in the constructor so they can never disagree.
- A registry backend, defaulting to the in-memory
  `aigear.management.v2.fake_registry.FakeRegistryV2` when none is injected.
  There is no real Firestore/GCS-backed V2 registry yet (that is T13 and
  later phases), so `PipelineAssetManagement` only makes sense in tests and
  local experimentation right now.

Only two methods are implemented, both read-only lookups against whatever
registry was injected:

```python
from aigear.management.pipeline_asset import PipelineAssetManagement
from aigear.management.v2.environment import EnvironmentIdentity

identity = EnvironmentIdentity(
    environment_id="production",
    gcp_project_number="123456789012",
    project_name="aigear_sklearn_pipeline",
    pipeline_version="logistic_regression",
    asset_bucket_name="aigear-prod-assets",
    asset_bucket_location="asia-northeast1",
    kms_trust_domain="projects/my-project/locations/asia-northeast1/keyRings/aigear",
)

manager = PipelineAssetManagement(identity)  # uses a fresh FakeRegistryV2

asset = manager.get_asset(asset_version_id)          # AssetVersionRecord | None
occurrence = manager.get_occurrence(occurrence_id)    # OccurrenceRecord | None
```

`asset_version_id` / `occurrence_id` may be passed as bare 64-char hex, a
`sha256:<hex>` typed string, or a `TypedId` — all three are accepted.

To exercise this against records you created yourself (e.g. in a test),
inject a `FakeRegistryV2` and populate it directly:

```python
from aigear.management.v2.fake_registry import FakeRegistryV2

registry = FakeRegistryV2()
registry.put_asset_version(my_asset_version_record)

manager = PipelineAssetManagement(identity, registry=registry)
assert manager.get_asset(my_asset_version_record.asset_version_id) is my_asset_version_record
```

## What is not implemented yet

Every other method named in spec 24.1 raises `NotImplementedError` on
purpose. This is a deliberate design constraint for Phase A: none of these
methods are allowed to silently fall back to `VersionedAssetManagement` (V1)
behavior, because that would hide the fact that V2 execution semantics
(attempt fencing, exact-ref finalize transactions, staged uploads, etc.)
are not actually enforced yet.

| Method | Needed for | Planned phase |
| --- | --- | --- |
| `begin_run` | Run creation, idempotency key handling | Phase B |
| `resolve_inputs` | Exact-ref input resolution per step | Phase B |
| `begin_attempt` | Attempt leasing / fencing token issuance | Phase B |
| `finalize_step_outputs` | All-or-nothing output commit transaction | Phase B |
| `fail_attempt` | Attempt failure + retry bookkeeping | Phase B |
| `cancel_run` | Run cancellation propagation | Phase B |
| `upload_asset` / `upload_bundle` | Staged upload + content-addressed commit | Phase B/C |
| `import_external` | External asset import with attestation | Phase C |
| `download_exact` | Exact-ref content download from real GCS | Phase B |

Calling any of these today raises `NotImplementedError` with a message
pointing back to this document; that is expected, not a bug.

## Recommendation for current code

Keep using `VersionedAssetManagement` for anything that runs today. Only
reach for `PipelineAssetManagement` in new tests/prototypes that exercise
the V2 record types and the fake registry directly, and re-check this guide
before depending on any additional method — it will be updated as each
phase lands.
