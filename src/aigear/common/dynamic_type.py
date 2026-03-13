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
    forced_generate: bool = False
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
        formatters = []
    )
    return None


def generate_schema_for_json(
    json_data,
    input_file_type: InputFileType = InputFileType.Json,
    output: str | Path = None,
    output_model_type: DataModelType = DataModelType.PydanticBaseModel,
    class_name: str = None,
    forced_generate: bool = False
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
        formatters = []
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
        forced_generate=True
    )


if __name__ == "__main__":
    with open("../template/env.sample.json", "r", encoding="utf-8") as f:
        all_cfg = json.load(f)

    output_path = Path.cwd() / "./schema/config_schema.py"
    generate_schema_for_json(
        json_data=all_cfg["aigear"],
        input_file_type=InputFileType.Json,
        output=output_path,
        output_model_type=DataModelType.PydanticBaseModel,
        class_name="Config",
        forced_generate=True
    )

    from aigear.common.schema.config_schema import Config

    Config(**all_cfg["aigear"])
