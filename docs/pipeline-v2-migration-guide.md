# Migrating from `VersionedAssetManagement` to `PipelineAssetManagement`

> Status: Phase B (see `docs/pipeline-v2-phase-b-tasks.md`, task T28). This
> guide describes the *current* state of the migration, which is
> intentionally incomplete. Do not use `PipelineAssetManagement` for
> production workloads yet — it only talks to in-memory fakes, never real
> Firestore/GCS — see [What is not implemented yet](#what-is-not-implemented-yet).

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
- A `GcsLayoutV2`, derived from that same `EnvironmentIdentity` (its
  `asset_bucket_name`/`project_name`/`pipeline_version` are exactly
  `GcsLayoutV2`'s three fields).
- Registry and GCS backends, defaulting to the in-memory
  `aigear.management.v2.fake_registry.FakeRegistryV2` and
  `aigear.management.v2.fake_gcs.FakeGcsClient` when none is injected. There
  is no real Firestore/GCS-backed V2 registry yet (that is "Phase B-infra"
  in `docs/pipeline-v2-phase-b-tasks.md` section 7), so
  `PipelineAssetManagement` only makes sense in tests and local
  experimentation right now.

The two read-only lookups (`get_asset`/`get_occurrence`) plus the full
Run/Step/Attempt execution lifecycle are implemented, against those two
in-memory fakes:

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

manager = PipelineAssetManagement(identity)  # uses a fresh FakeRegistryV2/FakeGcsClient

asset = manager.get_asset(asset_version_id)          # AssetVersionRecord | None
occurrence = manager.get_occurrence(occurrence_id)    # OccurrenceRecord | None

run = manager.begin_run(run_spec, idempotency_key, owner_principal="scheduler@aigear")
step = manager.resolve_inputs(run.run_id, "train", now=now)          # once its dependencies are committed
attempt = manager.begin_attempt(run.run_id, "train", owner_principal="worker-vm-1@aigear", now=now)
# ... worker stages its outputs via GcsLayoutV2.staging + manager.gcs.put_object ...
outcome = manager.finalize_step_outputs(step_completion_message, now=now)
manager.cancel_run(run.run_id, "operator requested cancellation")
manager.fail_attempt(run.run_id, "train", attempt.attempt_no,
                      fencing_token=attempt.fencing_token, retryable=True, reason="worker crashed")
path = manager.download_exact(None, occurrence.occurrence_id, target_path=local_path, now=now)
```

`asset_version_id` / `occurrence_id` may be passed as bare 64-char hex, a
`sha256:<hex>` typed string, or a `TypedId` — all three are accepted.

Spec 24.1 gives each of these methods only its business-level parameters
(`run_spec`/`idempotency_key`, `run_id`/`step_name`, ...); it never says how
a caller supplies cross-cutting inputs like the current time or "who is
calling". This class asks for those explicitly instead of guessing a
default:

- `now` and `owner_principal` are required keyword-only parameters on every
  method that needs them.
- `finalize_step_outputs` additionally needs `schema_contract_digest`/
  `runtime_contract_digest`/`policy_version` (spec 8.2's schema/policy pins),
  and `download_exact` needs the currently bound `ControlDocument`. Both are
  optional *constructor* parameters — supplying only some of a group is
  treated the same as supplying none — so the zero-config constructor above
  keeps working for callers that never touch those two methods; each one
  raises `PipelineAssetManagementError` if called without its required
  inputs configured.
- `begin_run` is the only place a `RunSpec` is ever supplied; every other
  method only takes a `run_id`, so `PipelineAssetManagement` caches the
  accepted `RunSpec` per `run_id` in memory and looks it up for them. This
  means every one of the above methods (other than `begin_run` itself) must
  be called on the *same* `PipelineAssetManagement` instance that accepted
  that Run.

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

`upload_asset`, `upload_bundle` and `import_external` still raise
`NotImplementedError` on purpose. This is a deliberate design constraint:
none of these methods are allowed to silently fall back to
`VersionedAssetManagement` (V1) behavior, because that would hide the fact
that manual asset ingestion (spec sections 12/13: external import,
attestation, trust bootstrapping) is not actually enforced yet.

| Method | Needed for | Planned phase |
| --- | --- | --- |
| `upload_asset` / `upload_bundle` | Staged upload + content-addressed commit for manually-produced assets | Phase C |
| `import_external` | External asset import with attestation | Phase C |

Calling any of these today raises `NotImplementedError` with a message
pointing back to this document; that is expected, not a bug.

## Recommendation for current code

Keep using `VersionedAssetManagement` for anything that runs today.
`PipelineAssetManagement`'s execution lifecycle (`begin_run` through
`download_exact`) is implemented and tested, but only against in-memory
fakes — there is still no real Firestore/GCS backend behind it, so it is not
yet safe for production workloads. Reach for it in new tests/prototypes that
exercise the V2 record types and execution lifecycle directly, and re-check
this guide before depending on any additional method — it will be updated as
each phase lands.
