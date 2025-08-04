from pathlib import Path
from tabulate import tabulate
from typing import (
    Optional,
    List,
)
from aigear.common.logger import logger
from .db_models import PipelineMeta
from .db_service import DBService


class PipelineManager:
    def __init__(
        self,
        pipeline_dir: str = None
    ):
        """
        Manage pipelines locally
        Args:
            pipeline_dir: pipeline folder. If it is None, put it together with the model
        """
        if pipeline_dir is None:
            pipeline_dir = Path.cwd() / "models"
        pipeline_dir.mkdir(parents=True, exist_ok=True)
        self.db = DBService(pipeline_dir)

        self._headers = [
            "pipeline_name", "version", "tags",
            "author", "description",
            "created_at", "updated_at",
        ]

    def list(
        self,
        pipeline_name: Optional[str] = None
    ):
        """
        Retrieve all pipelines or all versions of a specified pipeline
        Args:
            pipeline_name (str, optional):  Name of the pipeline. If left blank, all pipelines will be printed

        Returns:

        """
        pipeline_metas = self.db.get_metas(
            name=pipeline_name,
            db_model=PipelineMeta,
        )
        pipeline_meta_list = []
        for pipeline_meta in pipeline_metas:
            pipeline_meta_list.append([
                pipeline_meta.name, pipeline_meta.version, pipeline_meta.tags,
                pipeline_meta.author, pipeline_meta.description,
                pipeline_meta.created_at, pipeline_meta.updated_at,
            ])

        if pipeline_meta_list:
            pipelines_table = tabulate(pipeline_meta_list, headers=self._headers, tablefmt="grid")
            logger.info(f"[PM-List] Available pipelines: \n{pipelines_table}")
        else:
            logger.info("[PM-List] No pipelines available.")

    def register(
        self,
        pipeline_name: str,
        version: Optional[str] = None,
        author: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ):
        """
        Registration pipeline information
        Args:

            pipeline_name (str): Name of the pipeline
            version: (str, optional): Version of the pipeline. If it is empty, it will automatically increase.
            author (str, optional): Author of the pipeline
            description (str, optional): Description of the pipeline
            tags (list[str], optional): Tags of the pipeline.

        Returns:

        """
        if version is None:
            version = self.db.get_next_version(
                name=pipeline_name,
                db_model=PipelineMeta,
            )

        pipeline_meta = PipelineMeta(
            author=author,
            description=description,
            name=pipeline_name,
            version=version,
            tags=tags,
        )
        self.db.add_meta(
            db_mate=pipeline_meta,
            db_model=PipelineMeta
        )
        logger.info(f"[PM-Register] Pipeline({pipeline_name}) has been registered.")

    def delete(
        self,
        pipeline_name: str,
        version: Optional[str] = None,
    ):
        """
        Delete the specified version of the pipeline
        Args:
            pipeline_name (str): Name of the pipeline
            version (str, optional): Version of the pipeline. If it is empty, delete all versions of the pipeline

        Returns:

        """
        if version is None:
            self.db.delete_meta(
                name=pipeline_name,
                db_model=PipelineMeta,
            )
            logger.info(f"[PM-Del] All versions of the {pipeline_name} pipeline have been deleted.")
        else:
            self.db.delete_meta(
                name=pipeline_name,
                version=version,
                db_model=PipelineMeta,
            )
            logger.info(f"[PM-Del] the pipeline({pipeline_name}_{version}) have been deleted.")

    # def rename(self):
    #     pass
