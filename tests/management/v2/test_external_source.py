from __future__ import annotations

import pytest

from aigear.management.v2.external_source import (
    AllowedExternalBucket,
    ExternalSourceError,
    ExternalSourcePolicy,
    parse_exact_external_source,
)


@pytest.fixture()
def policy():
    return ExternalSourcePolicy(
        target_environment_id="production",
        allowed_buckets=(
            AllowedExternalBucket(
                project_id="source-project",
                bucket="approved.bucket",
                region="asia-east1",
            ),
        ),
        max_size_bytes=1024,
    )


def test_exact_gcs_source_is_parsed_from_allowlist(policy):
    source = parse_exact_external_source(
        "gs://approved.bucket/models/model.onnx#123",
        policy=policy,
        observed_region="asia-east1",
        observed_size_bytes=42,
    )
    assert source.project_id == "source-project"
    assert source.object_name == "models/model.onnx"
    assert source.generation == "123"


@pytest.mark.parametrize(
    "uri,error",
    [
        ("https://approved.bucket/model#1", "gs://"),
        ("gs://approved.bucket/model", "generation"),
        ("gs://approved.bucket/model#latest", "generation"),
        ("gs://approved.bucket/model?X-Goog-Signature=x#1", "query"),
        ("gs://approved.bucket/a//b#1", "unsafe"),
        ("gs://approved.bucket/a/../b#1", "unsafe"),
        ("gs://approved.bucket/a%2Fb#1", "forbidden"),
        ("gs://approved.bucket/模型#1", "ASCII"),
    ],
)
def test_mutable_or_ambiguous_sources_are_rejected(policy, uri, error):
    with pytest.raises(ExternalSourceError, match=error):
        parse_exact_external_source(
            uri,
            policy=policy,
            observed_region="asia-east1",
            observed_size_bytes=42,
        )


def test_non_allowlisted_bucket_is_rejected(policy):
    with pytest.raises(ExternalSourceError, match="allowlisted"):
        parse_exact_external_source(
            "gs://attacker.bucket/model#1",
            policy=policy,
            observed_region="asia-east1",
            observed_size_bytes=42,
        )


def test_region_mismatch_is_rejected(policy):
    with pytest.raises(ExternalSourceError, match="region"):
        parse_exact_external_source(
            "gs://approved.bucket/model#1",
            policy=policy,
            observed_region="us-central1",
            observed_size_bytes=42,
        )


def test_oversized_source_is_rejected(policy):
    with pytest.raises(ExternalSourceError, match="size"):
        parse_exact_external_source(
            "gs://approved.bucket/model#1",
            policy=policy,
            observed_region="asia-east1",
            observed_size_bytes=1025,
        )


def test_duplicate_bucket_policy_is_rejected():
    bucket = AllowedExternalBucket(
        project_id="source-project",
        bucket="approved.bucket",
        region="asia-east1",
    )
    with pytest.raises(ExternalSourceError, match="unique"):
        ExternalSourcePolicy(
            target_environment_id="production",
            allowed_buckets=(bucket, bucket),
            max_size_bytes=1024,
        )
