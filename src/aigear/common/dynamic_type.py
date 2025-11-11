import os
import json
import logging
from pathlib import Path
from datamodel_code_generator import generate, DataModelType, InputFileType


def generate_schema(
        input_path: str,
        input_file_type: InputFileType = InputFileType.Json,
        output: str = None,
        output_model_type: DataModelType = DataModelType.PydanticBaseModel,
        class_name: str = None,
        forced_generate: bool = False
):
    if input_path:
        input_path = Path(input_path)
    else:
        logging.error("No file path for [generate_dynamic_type].")
        return None

    if output:
        output = Path(output)

    if not forced_generate:
        if os.path.exists(output):
            logging.info("The file that needs to be generated already exists.")
            return None

    generate(
        input_=input_path,
        input_file_type=input_file_type,
        output=output,
        output_model_type=output_model_type,
        class_name=class_name,
    )
    return None


def generate_schema_for_json(
    json_data,
    input_file_type: InputFileType = InputFileType.Json,
    output: str = None,
    output_model_type: DataModelType = DataModelType.PydanticBaseModel,
    class_name: str = None,
    forced_generate: bool = False
):
    if output:
        output = Path(output)

    if not forced_generate:
        if os.path.exists(output):
            logging.info("The file that needs to be generated already exists.")
            return None

    cfg_text = json.dumps(json_data)
    generate(
        input_=cfg_text,
        input_file_type=input_file_type,
        output=output,
        output_model_type=output_model_type,
        class_name=class_name,
    )
    return None

if __name__=="__main__":
    with open("../../../env.json", "r", encoding="utf-8") as f:
        cfg = json.load(f)

    output_path = os.path.join(os.getcwd(), "./schema/config_schema.py")
    generate_schema_for_json(
        json_data=cfg["aigear"],
        input_file_type=InputFileType.Json,
        output=output_path,
        output_model_type=DataModelType.PydanticBaseModel,
        class_name="Config",
        forced_generate=True
    )

    from aigear.common.schema.config_schema import Config
    Config(**cfg["aigear"])
