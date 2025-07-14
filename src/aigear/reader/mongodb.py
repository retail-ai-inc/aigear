from pymongo import MongoClient
from pymongoarrow.monkey import patch_all
import logging
import weakref
from aigear.secretmanager import SecretManager

patch_all()


class MDBClient:
    def __init__(self, project_id):
        self.project_id = project_id
        self.client = None
        weakref.finalize(self, self.close)

    def connect_db(self, mongo_uri, db_name):
        self.client = MongoClient(mongo_uri)
        db = self.client[db_name]
        return db

    def connect_db_by_secret(self, mongo_uri_secret=None, db_name_secret=None):
        secret_manager = SecretManager(self.project_id)
        if mongo_uri_secret and db_name_secret:
            mongo_uri = secret_manager.get_secret_val(mongo_uri_secret)
            db_name = secret_manager.get_secret_val(db_name_secret)

            db = self.connect_db(mongo_uri, db_name)
            return db
        else:
            logging.info("Please check if the mongodb is configured correctly.")

    def close(self):
        if self.client is not None:
            # Close database link during recycling
            self.client.close()
