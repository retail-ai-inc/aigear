import os
import re
import shutil
import pickle
import cloudpickle
from pathlib import Path
from tabulate import tabulate
from typing import List
from ..common.logger import logger


class ModelManager:
    def __init__(
        self,
        model_dir: str = None
    ):
        """
        Manage models locally
        Args:
            model_dir: Model folder. If it is None, the models folder will be created in the current path
        """
        if model_dir is None:
            model_dir = Path.cwd() / "models"
        model_dir.mkdir(parents=True, exist_ok=True)

        self.models_dir: Path = model_dir
        self._headers = ["model_name", "version", "path"]
        self._model_list = []
        self._init_version = "0.0.1"

    def save(
        self,
        model: object,
        model_name: str,
        version: str = None
    ):
        """
        Save the model to the model management folder
        Args:
            model: Object of the model
            model_name: Name of the model
            version: Version of the model. If it is empty, it will automatically increase from `0.0.1`

        Returns: model path

        """
        models_subdir = self.models_dir / model_name
        models_subdir.mkdir(parents=True, exist_ok=True)

        version = self.set_version(model_name, version)
        model_path = models_subdir / f"{model_name}_{version}.pkl"
        if model_path.exists():
            logger.info(f"Model already exists {model_name}_{version}.pkl")
            return model_path

        with open(model_path, "wb") as pickle_file:
            cloudpickle.dump(model, pickle_file)
        logger.info(f"Model saved at {model_name}_{version}.pkl")
        return model_path

    def load(
        self,
        model_name: str,
        version: str = None
    ):
        """
        Load the model from the model management folder
        Args:
            model_name: Name of the model
            version: Version of the model

        Returns:

        """
        if version is None:
            version = self.get_latest_version(model_name)
        model_path = self.models_dir / model_name / f"{model_name}_{version}.pkl"
        if not model_path.exists():
            logger.info(f"Model path {model_name}_{version}.pkl does not exist")
            return None

        with open(model_path, "rb") as pickle_file:
            model = pickle.load(pickle_file)
        logger.info(f"Model loaded from {model_name}_{version}.pkl")
        return model

    def list(
        self,
        model_name: str = None
    ):
        if model_name is None:
            models = [f"{f.parent.name}/{f.name}" for f in self.models_dir.glob("**/*.pkl")]
        else:
            models = [f"{f.parent.name}/{f.name}" for f in self.models_dir.glob(f"{model_name}/*.pkl")]
        if models:
            for model_path in models:
                model_name, model_version = model_path.split("/")
                model_version = self._extract_version(model_version)
                self._model_list.append([model_name, model_version, model_path])
            models_table = tabulate(self._model_list, headers=self._headers, tablefmt="grid")
            logger.info(f"Available models: \n{models_table}")
        else:
            logger.info("No models available.")

    def delete(
        self,
        model_name: str,
        version: str,
    ):
        model_path = self.models_dir / model_name / f"{model_name}_{version}.pkl"
        try:
            model_path.unlink()
            logger.info(f"Model {model_name}_{version}.pkl already deleted.")
        except FileNotFoundError:
            logger.info(f"Model {model_name}_{version}.pkl does not exist.")
        except Exception as e:
            logger.error(f"An error occurred：{e}")

    def upload(
        self,
        model_file_path: str,
        model_name: str,
        version: str,
    ):
        model_subdir_path = self.models_dir / model_name
        model_subdir_path.mkdir(parents=True, exist_ok=True)
        target_file = model_subdir_path / f"{model_name}_{version}.pkl"
        if target_file.exists():
            logger.info(f"Model already exists {model_name}_{version}.pkl")
            return target_file

        source_file = Path(model_file_path)
        if not source_file.exists():
            shutil.copy(source_file, target_file)
            logger.info(f"Model uploaded to {model_name}/{model_name}_{version}.pkl")
        else:
            logger.info(f"Model file {model_file_path} does not exist")

    def get(
        self,
        model_name: str,
        version: str = None,
    ):
        if version is None:
            version = self.get_latest_version(model_name)
        model_path = self.models_dir / model_name / f"{model_name}_{version}.pkl"
        if not model_path.exists():
            logger.info(f"Model path {model_path} does not exist")
            return None
        return model_path

    def update(
        self,
        model: object,
        model_name: str,
        version: str,
    ):
        model_path = self.models_dir / model_name / f"{model_name}_{version}.pkl"
        with open(model_path, "wb") as pickle_file:
            cloudpickle.dump(model, pickle_file)
        logger.info(f"Model saved at {model_name}/{model_name}_{version}.pkl")
        return model_path

    @staticmethod
    def _extract_version(file_name: str):
        pattern = r'_(\d+\.\d+\.\d+)\.'
        match = re.search(pattern, file_name)
        if match:
            return match.group(1)
        return ""

    def _get_latest_version(
        self,
        model_file_list: str,
    ):
        if not model_file_list:
            return None

        versions_list = [self._extract_version(f.__str__()) for f in model_file_list]
        versions_list = sorted(versions_list, key=lambda s: list(map(int, s.split('.'))))
        return versions_list[-1]

    def _get_next_version(
        self,
        model_file_list: List[str],
    ):
        latest_version = self._get_latest_version(model_file_list)
        if not latest_version:
            return "0.0.1"

        major, minor, patch = map(int, latest_version.split('.'))
        patch += 1
        return f"{major}.{minor}.{patch}"

    def set_version(
        self,
        model_name: str,
        version: str,
    ):
        if version is None:
            model_file_list = [f.__str__() for f in self.models_dir.glob(f"{model_name}/*.pkl")]
            version = self._get_next_version(model_file_list)
        return version

    def get_latest_version(
        self,
        model_name: str,
    ):
        model_file_list = [f for f in self.models_dir.glob(f"{model_name}/*.pkl")]
        version = self._get_latest_version(model_file_list)
        return version
