from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


def init_meta_db(path):
    engine = create_engine(f'sqlite:///{path}/meta.db')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)
    return session
