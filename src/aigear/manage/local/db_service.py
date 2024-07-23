from typing import (
    Optional,
    List,
    Union,
    Type,
)
from .db_models import ModelMeta, PipelineMeta
from .init_db import init_meta_db

DBModel = Type[Union[ModelMeta, PipelineMeta]]


class DBService:
    def __init__(
        self,
        db_dir: str = None
    ):
        """
        DB service
        Args:
            db_dir: The db folder.
        """
        if db_dir is None:
            raise ValueError("The database path cannot be empty.")
        self.session = init_meta_db(db_dir)

    def __del__(self):
        self.session.close_all()

    def get_meta(
        self,
        name: str,
        version: str,
        db_model: DBModel,
    ):
        with self.session() as session:
            meta = session.query(db_model).filter_by(name=name, version=version).order_by(
                db_model.id.desc()).first()
            return meta

    def get_metas(
        self,
        name: str,
        db_model: DBModel,
    ) -> List[DBModel]:
        with self.session() as session:
            if name is None:
                model_metas = session.query(db_model).order_by(db_model.name, db_model.version).all()
            else:
                model_metas = session.query(db_model).filter_by(
                    name=name).order_by(db_model.name, db_model.version).all()

            return model_metas

    def get_latest_version(
        self,
        name: str,
        db_model: DBModel,
    ):
        version = None
        with self.session.begin() as session:
            meta: ModelMeta = session.query(db_model).filter_by(name=name).order_by(
                db_model.id.desc()).first()
            if meta is not None:
                version = meta.version
        return version

    def get_next_version(
        self,
        name: str,
        db_model: DBModel,
    ):
        latest_version = self.get_latest_version(name, db_model)
        if latest_version is None:
            latest_version = "0.0.0"
        major, minor, patch = map(int, latest_version.split('.'))
        patch += 1
        return f"{major}.{minor}.{patch}"

    def add_meta(
        self,
        db_mate: Union[ModelMeta, PipelineMeta],
        db_model: DBModel,
    ):
        with self.session.begin() as session:
            meta_from_db = session.query(db_model).filter_by(name=db_mate.name, version=db_mate.version).first()
            if not meta_from_db:
                session.add(db_mate)

    def delete_meta(
        self,
        name: str,
        version: Optional[str] = None,
        db_model: DBModel = None,
    ):
        if version is None:
            with self.session.begin() as session:
                meta_from_db = session.query(db_model).filter_by(name=name).all()
                for meta in meta_from_db:
                    session.delete(meta)
        else:
            with self.session.begin() as session:
                meta_from_db = session.query(db_model).filter_by(name=name, version=version).all()
                for meta in meta_from_db:
                    session.delete(meta)
