from __future__ import annotations

import pytest

from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.record_codec import RecordCodecError, decode_record, encode_record
from aigear.management.v2.records.run_spec import OutputSlotSpec, RunSpec, StepSpec


def _spec():
    digest = TypedId.from_bare("aa" * 32)
    return RunSpec(
        trigger_principal="scheduler@example.com",
        trigger_source="schedule",
        graph_digest=digest,
        code_digest=digest,
        config_digest=digest,
        producer_image_digest=digest,
        steps=(
            StepSpec(
                step_name="train",
                outputs=(OutputSlotSpec("model", "model", "model.onnx"),),
            ),
        ),
        retry_policy={"max_attempts": 3},
    )


def test_record_codec_losslessly_round_trips_nested_run_spec():
    record = _spec()
    assert decode_record(RunSpec, encode_record(record)) == record


def test_record_codec_rejects_unknown_firestore_fields():
    encoded = encode_record(_spec())
    encoded["unexpected"] = True
    with pytest.raises(RecordCodecError, match="unknown fields"):
        decode_record(RunSpec, encoded)
