import importlib.util
import sys

from aigear.common.dynamic_type import (
    DataModelType,
    InputFileType,
    generate_schema,
    generate_schema_for_json,
)

SAMPLE = {
    "name": "demo",
    "retries": 3,
    "nested": {"enabled": True, "threshold": 0.5, "tags": ["a", "b"]},
}


def _load(path, module_name):
    """Import a generated schema file the way a normal project would."""
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    return module


def test_default_model_type_exists_in_installed_generator():
    # datamodel-code-generator renames/removes DataModelType members between releases;
    # the defaults of the helpers below are evaluated at import time, so this must hold.
    assert hasattr(DataModelType, "PydanticV2BaseModel")


def test_generate_schema_for_json_default_model_type(tmp_path):
    out = tmp_path / "schema_default.py"
    generate_schema_for_json(json_data=SAMPLE, output=out, class_name="Demo")

    module = _load(out, "generated_schema_default")
    demo = module.Demo(**SAMPLE)
    assert demo.name == "demo"
    assert demo.retries == 3
    assert demo.nested.enabled is True
    assert demo.nested.tags == ["a", "b"]


def test_generate_schema_for_json_explicit_model_type(tmp_path):
    out = tmp_path / "schema_explicit.py"
    generate_schema_for_json(
        json_data=SAMPLE,
        input_file_type=InputFileType.Json,
        output=out,
        output_model_type=DataModelType.PydanticV2BaseModel,
        class_name="Demo",
    )
    module = _load(out, "generated_schema_explicit")
    assert module.Demo(**SAMPLE).nested.threshold == 0.5


def test_generate_schema_from_file(tmp_path):
    import json

    src = tmp_path / "sample.json"
    src.write_text(json.dumps(SAMPLE), encoding="utf-8")
    out = tmp_path / "schema_from_file.py"

    generate_schema(input_path=src, output=out, class_name="Demo")

    assert _load(out, "generated_schema_from_file").Demo(**SAMPLE).retries == 3


def test_existing_output_is_kept_unless_forced(tmp_path):
    out = tmp_path / "schema_keep.py"
    out.write_text("# keep me\n", encoding="utf-8")

    generate_schema_for_json(json_data=SAMPLE, output=out, class_name="Demo")
    assert out.read_text(encoding="utf-8") == "# keep me\n"

    generate_schema_for_json(
        json_data=SAMPLE, output=out, class_name="Demo", forced_generate=True
    )
    assert "class Demo" in out.read_text(encoding="utf-8")


def test_missing_output_path_is_a_no_op():
    assert generate_schema_for_json(json_data=SAMPLE, output=None) is None
    assert generate_schema(input_path=None, output=None) is None
