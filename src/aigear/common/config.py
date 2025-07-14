import json
import os
from typing import Optional
import logging
from .schema.config_schema import Config


def read_config() -> Optional[Config]:
    env_path = os.path.join(os.getcwd(), "env.json")
    if not os.path.exists(env_path):
        logging.error(f"Configuration file {env_path} not found.")
        return None

    with open(env_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    return Config(**cfg)
