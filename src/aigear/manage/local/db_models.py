from sqlalchemy import Column, Integer, String, DateTime, JSON
from datetime import datetime
from .init_db import Base


class ModelMeta(Base):
    __tablename__ = "model_meta"

    id = Column(Integer, primary_key=True, autoincrement=True)
    author = Column(String)
    description = Column(String)
    name = Column(String)
    version = Column(String)
    framework = Column(String)
    path = Column(String)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class PipelineMeta(Base):
    __tablename__ = "pipeline_meta"

    id = Column(Integer, primary_key=True, autoincrement=True)
    author = Column(String)
    description = Column(String)
    name = Column(String)
    version = Column(String)
    tags = Column(JSON)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
