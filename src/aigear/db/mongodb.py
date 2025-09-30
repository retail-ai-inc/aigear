from pymongo import MongoClient
from pymongoarrow.monkey import patch_all
import weakref
from ..common.secretmanager import SecretManager
from ..common.logger import create_stage_logger, PipelineStage

patch_all()

# Use preprocessing stage logger for database operations
db_logger = create_stage_logger(
    stage=PipelineStage.PREPROCESSING,
    module_name=__name__,
    cpu_count=1,
    memory_limit="2GB",
    enable_cloud_logging=False
)


class MDBClient:
    def __init__(self, project_id):
        self.project_id = project_id
        self.client = None
        weakref.finalize(self, self.close)

    def connect_db(self, mongo_uri, db_name):
        with db_logger.stage_context() as logger:
            logger.info(f"Connecting to MongoDB database: {db_name}")
            self.client = MongoClient(mongo_uri)
            db = self.client[db_name]
            logger.info("MongoDB connection established successfully")
            return db

    def connect_db_by_secret(self, mongo_uri_secret=None, db_name_secret=None):
        with db_logger.stage_context() as logger:
            secret_manager = SecretManager(self.project_id)
            if mongo_uri_secret and db_name_secret:
                logger.info("Retrieving MongoDB connection secrets")
                mongo_uri = secret_manager.get_secret_val(mongo_uri_secret)
                db_name = secret_manager.get_secret_val(db_name_secret)

                db = self.connect_db(mongo_uri, db_name)
                return db
            else:
                logger.warning("Please check if the mongodb is configured correctly.")
                return None

    def close(self):
        if self.client is not None:
            # Close database link during recycling
            self.client.close()
