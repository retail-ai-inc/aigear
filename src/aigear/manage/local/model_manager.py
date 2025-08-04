import shutil
import pickle
import cloudpickle
from pathlib import Path
from tabulate import tabulate
from typing import (
    Optional,
    Any,
)
from aigear.common.logger import logger
from .constraints import Constraints
from .db_models import ModelMeta
from .db_service import DBService


class ModelManager(Constraints):
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
        self.db = DBService(model_dir)

        self.models_dir: Path = Path(model_dir)
        self._headers = [
            "model_name", "version", "framework",
            "author", "description", "path",
            "created_at", "updated_at",
        ]

    def pickle_save(
        self,
        model: Any,
        model_name: str,
        version: Optional[str] = None,
        author: Optional[str] = None,
        description: Optional[str] = None,
        framework: Optional[str] = None,
    ):
        """
        Save the model to the model management folder
        If you want to overwrite the model, please specify the version number
        Args:
            model (Any): Object of the model.
            model_name (str): Name of the model.
            version (str, optional): Version of the model. If it is empty, it will automatically increase from `0.0.1`.
            author (str, optional): Author of the model
            description (str, optional): Description of the model
            framework (str, optional): Framework of the model.
                It can be tf(TensorFlow), py(pytorch), other(sklearn, other)

        Returns:
            pathlib.Path: The path where the model is saved.
        """
        models_subdir = self.models_dir / model_name
        models_subdir.mkdir(parents=True, exist_ok=True)

        if version is None:
            version = self.db.get_next_version(model_name, ModelMeta)
        model_path = models_subdir / f"{model_name}_{version}.pkl"
        if model_path.exists():
            logger.info(f"[MM-Save] Model already exists {model_name}/{model_name}_{version}.pkl")
            return model_path

        with open(model_path, "wb") as pickle_file:
            cloudpickle.dump(model, pickle_file)

        model_meta = ModelMeta(
            author=author,
            description=description,
            name=model_name,
            version=version,
            framework=framework,
            path=model_path.__str__(),
        )
        self.db.add_meta(
            db_mate=model_meta,
            db_model=ModelMeta
        )
        logger.info(f"[MM-Save] Model saved at {model_name}/{model_name}_{version}.pkl")

        return model_path

    def pickle_load(
        self,
        model_name: str,
        version: Optional[str] = None
    ):
        """
        Load the model from the model management folder
        Args:
            model_name (str): Name of the model
            version (str, optional): Version of the model. If it is empty, the latest version will be loaded

        Returns:
            Model: The loaded model instance.
        """
        if version is None:
            version = self.db.get_latest_version(model_name, ModelMeta)
        if version is None:
            logger.info(f"[MM-Load] No models available: {model_name}")
            return None
        model_meta = self.db.get_meta(model_name, version, ModelMeta)
        model_path = model_meta.path
        if model_path is None:
            logger.info(f"[MM-Load] Model {model_name} not found in the registry")
            return None

        if not Path(model_path).exists():
            logger.info(f"[MM-Load] Model path does not exist: {model_path}")
            return None

        with open(model_path, "rb") as pickle_file:
            model = pickle.load(pickle_file)
        logger.info(f"[MM-Load] Model loaded from {model_path}")
        return model

    def list(
        self,
        model_name: Optional[str] = None
    ):
        """
        Retrieve all models or all versions of a specified model
        Args:
            model_name (str, optional):  Name of the model. If left blank, all models will be printed

        Returns:

        """
        model_metas = self.db.get_metas(
            name=model_name,
            db_model=ModelMeta,
        )
        model_meta_list = []
        for model_meta in model_metas:
            model_meta_list.append([
                model_meta.name, model_meta.version, model_meta.framework,
                model_meta.author, model_meta.description, model_meta.path,
                model_meta.created_at, model_meta.updated_at,
            ])

        if model_meta_list:
            models_table = tabulate(model_meta_list, headers=self._headers, tablefmt="grid")
            logger.info(f"[MM-List] Available models: \n{models_table}")
        else:
            logger.info("[MM-List] No models available.")

    def register(
        self,
        model_file_path: str,
        model_name: str,
        version: Optional[str] = None,
        author: Optional[str] = None,
        description: Optional[str] = None,
        framework: Optional[str] = None,
    ):
        """
        Upload the model. If the model already exists, it will be overwritten
        Args:

            model_file_path (str): Path of the model
            model_name (str): Name of the model
            version: (str, optional): Version of the model. If it is empty, it will automatically increase.
            author (str, optional): Author of the model
            description (str, optional): Description of the model
            framework (str, optional): Framework of the model.
                It can be tf(TensorFlow), py(pytorch), other(sklearn, other)

        Returns:

        """
        if version is None:
            version = self.db.get_next_version(
                name=model_name,
                db_model=ModelMeta,
            )
        model_subdir_path = self.models_dir / model_name
        model_subdir_path.mkdir(parents=True, exist_ok=True)

        source_file = Path(model_file_path)
        if source_file.is_file():
            target_file_name = f"{model_name}_{version}{source_file.suffix}"
        elif source_file.is_dir():
            target_file_name = f"{model_name}_{version}"
        else:
            target_file_name = ""
            logger.info(f"[MM-Register] Only supports model files or folders")

        target_file = model_subdir_path / target_file_name
        if target_file.exists():
            logger.info(f"[MM-Register] Model already exists: {target_file_name}")
            return target_file

        if source_file.exists():
            shutil.copy(source_file, target_file)

            model_meta = ModelMeta(
                author=author,
                description=description,
                name=model_name,
                version=version,
                framework=framework,
                path=target_file.__str__(),
            )
            self.db.add_meta(
                db_mate=model_meta,
                db_model=ModelMeta
            )
            logger.info(f"[MM-Register] Model uploaded to {model_name}/{target_file_name}")
        else:
            logger.info(f"[MM-Register] Model file does not exist: {model_file_path}")

    def path(
        self,
        model_name: str,
        version: str = None,
    ):
        """
        Obtain the path of the model
        Args:
            model_name (str): Name of the model.
            version (str, optional): Version of the model. If empty, the latest version will be obtained.

        Returns:
            pathlib.Path: The path of the model.
        """
        if version is None:
            version = self.db.get_latest_version(
                name=model_name,
                db_model=ModelMeta,
            )
        if version is None:
            logger.info(f"[MM-Path] No models available: {model_name}")
            return None

        model_meta = self.db.get_meta(model_name, version, ModelMeta)
        model_path = model_meta.path
        if model_path is None:
            logger.info(f"[MM-Path] Model {model_name} not found in the registry")
            return None

        if not Path(model_path).exists():
            logger.info(f"[MM-Path] Model path {model_path} does not exist")
            return None
        return model_path

    def delete(
        self,
        model_name: str,
        version: Optional[str] = None,
    ):
        """
        Delete the specified version of the model
        Args:
            model_name (str): Name of the model
            version (str, optional): Version of the model. If it is empty, delete all versions of the model

        Returns:

        """
        if version is None:
            model_folder = self.models_dir / model_name
            if model_folder.exists():
                shutil.rmtree(model_folder)
                self.db.delete_meta(
                    name=model_name,
                    db_model=ModelMeta,
                )
                logger.info(f"[MM-Del] All versions of the {model_name} model have been deleted.")
            else:
                logger.info(f"[MM-Del] Model {model_name} does not exist.")
            return None

        model_meta = self.db.get_meta(model_name, version, ModelMeta)
        model_path = model_meta.path
        if model_path is None:
            logger.info(f"[MM-Path] Model {model_name}_{version} not found in the registry")
            return None

        model_path = Path(model_path)
        if model_path.exists():
            model_path.unlink()
            self.db.delete_meta(
                name=model_name,
                version=version,
                db_model=ModelMeta,
            )
            logger.info(f"[MM-Del] Model already deleted: {model_path}.")
        else:
            logger.info(f"[MM-Del] Model does not exist: {model_path}.")

    # def rename(self):
    #     pass
