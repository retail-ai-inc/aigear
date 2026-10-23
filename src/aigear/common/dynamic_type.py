import json
import logging
from pathlib import Path
from datamodel_code_generator import generate, DataModelType, InputFileType


def generate_schema(
    input_path: str | Path = None,
    input_file_type: InputFileType = InputFileType.Json,
    output: str | Path = None,
    output_model_type: DataModelType = DataModelType.PydanticBaseModel,
    class_name: str = None,
    forced_generate: bool = False,
):
    if input_path is None:
        logging.warning("No input path set.")
        return None
    if isinstance(input_path, str):
        input_path = Path(input_path)

    if output is None:
        logging.warning("No output path set.")
        return None
    if isinstance(output, str):
        output = Path(output)

    if not forced_generate and output.exists():
        logging.info("The file that needs to be generated already exists.")
        return None

    generate(
        input_=input_path,
        input_file_type=input_file_type,
        output=output,
        output_model_type=output_model_type,
        class_name=class_name,
        formatters=[],
    )
    return None


def generate_schema_for_json(
    json_data,
    input_file_type: InputFileType = InputFileType.Json,
    output: str | Path = None,
    output_model_type: DataModelType = DataModelType.PydanticBaseModel,
    class_name: str = None,
    forced_generate: bool = False,
):
    if output is None:
        logging.warning("No output path set.")
        return None

    if isinstance(output, str):
        output = Path(output)

    if not forced_generate and output.exists():
        logging.info("The file that needs to be generated already exists.")
        return None

    cfg_text = json.dumps(json_data)
    generate(
        input_=cfg_text,
        input_file_type=input_file_type,
        output=output,
        output_model_type=output_model_type,
        class_name=class_name,
        formatters=[],
    )
    return None


def auto_aigear_schema():
    current_path = Path.cwd()
    env_sample = current_path / "src/aigear/template/env.sample.json"
    output_path = current_path / "src/aigear/common/schema/config_schema.py"
    with open(env_sample, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    generate_schema_for_json(
        json_data=cfg["aigear"],
        input_file_type=InputFileType.Json,
        output=output_path,
        output_model_type=DataModelType.PydanticBaseModel,
        class_name="Config",
        forced_generate=True,
    )


def generate_aigear_config_schema(
    env_sample: str | Path = None,
    output_path: str | Path = None,
) -> None:
    """Generate, correct, and validate the framework configuration schema."""
    import runpy

    common_path = Path(__file__).resolve().parent
    env_sample = (
        Path(env_sample) if env_sample is not None else common_path.parent / "template/env.sample.json"
    )
    output_path = (
        Path(output_path) if output_path is not None else common_path / "schema/config_schema.py"
    )
    with open(env_sample, "r", encoding="utf-8") as f:
        all_cfg = json.load(f)

    generate_schema_for_json(
        json_data=all_cfg["aigear"],
        input_file_type=InputFileType.Json,
        output=output_path,
        output_model_type=DataModelType.PydanticBaseModel,
        class_name="Config",
        forced_generate=True,
    )

    corrections = {
        "from pydantic import BaseModel": (
            "from pydantic import BaseModel, ConfigDict, Field\nfrom typing import Optional"
        ),
        **{
            f"class {name}(BaseModel):": (
                f"class {name}(BaseModel):\n    model_config = ConfigDict(extra=\"forbid\")"
            )
            for name in ("Bucket", "TrainBucket", "ReleaseBucket")
        },
        "    trash_retention_days: int": (
            "    trash_retention_days: int = Field(default=30, strict=True, gt=0)"
        ),
        "    event: str": "    event: str = \"push\"",
        "    branch_pattern: str": "    branch_pattern: Optional[str] = None",
        "    tag_pattern: str": "    tag_pattern: Optional[str] = None",
        "    ms_image_name: str": "    ms_image_name: Optional[str] = None",
        "    pl_image_name: str": "    pl_image_name: Optional[str] = None",
        "    image_tag: str": "    image_tag: Optional[str] = \"latest\"",
        "    slack: Slack": "    slack: Optional[Slack] = None",
    }
    source = output_path.read_text(encoding="utf-8")
    for original, corrected in corrections.items():
        if source.count(original) != 1:
            raise ValueError(f"Expected one generated declaration: {original}")
        source = source.replace(original, corrected)
    output_path.write_text(source, encoding="utf-8")

    # Validate the corrected file rather than a cached imported model.
    namespace = runpy.run_path(str(output_path))
    model = namespace["Config"]
    model.model_rebuild(_types_namespace=namespace)
    model.model_validate(all_cfg["aigear"])


if __name__ == "__main__":
    generate_aigear_config_schema()
