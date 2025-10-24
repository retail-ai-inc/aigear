from aigear.common.config import AigearConfig
from aigear.infrastructure.gcp.bucket import Bucket
from aigear.infrastructure.gcp.build import CloudBuild
from aigear.infrastructure.gcp.function import CloudFunction
from aigear.infrastructure.gcp.iam import ServiceAccounts
from aigear.infrastructure.gcp.pub_sub import PubSub
from aigear.infrastructure.gcp.artifacts import Artifacts
from aigear.infrastructure.gcp.constant import (
    entry_point_of_cloud_fuction,
)
from aigear.common.logger import Logging


logger = Logging(log_name=__name__).console_logging()

class Infra:
    def __init__(self):
        self.aigear_config = AigearConfig.get_config()
        self.service_account = f"{self.aigear_config.gcp.iam.account_name}@{self.aigear_config.gcp.gcp_project_id}.iam.gserviceaccount.com"
        self.model_bucket = Bucket(
            bucket_name=self.aigear_config.gcp.bucket.bucket_name,
            location=self.aigear_config.gcp.location,
            project_id=self.aigear_config.gcp.gcp_project_id,
        )
        self.release_model_bucket = Bucket(
            bucket_name=self.aigear_config.gcp.bucket.bucket_name_for_release,
            location=self.aigear_config.gcp.location,
            project_id=self.aigear_config.gcp.gcp_project_id,
        )
        self.cloud_build = CloudBuild(
            trigger_name=self.aigear_config.gcp.cloud_build.trigger_name,
            description=self.aigear_config.gcp.cloud_build.description,
            repo_owner=self.aigear_config.gcp.cloud_build.repo_owner,
            repo_name=self.aigear_config.gcp.cloud_build.repo_name,
            branch_pattern=self.aigear_config.gcp.cloud_build.branch_pattern,
            build_config=self.aigear_config.gcp.cloud_build.build_config,
            region=self.aigear_config.gcp.location,
            substitutions=self.aigear_config.gcp.cloud_build.substitutions,
            project_id=self.aigear_config.gcp.gcp_project_id,
        )
        self.cloud_function = CloudFunction(
            function_name=self.aigear_config.gcp.cloud_function.function_name,
            region=self.aigear_config.gcp.location,
            entry_point=entry_point_of_cloud_fuction,
            topic_name=self.aigear_config.gcp.pub_sub.topic_name,
            project_id=self.aigear_config.gcp.gcp_project_id,
            service_account=self.service_account
        )
        self.service_accounts = ServiceAccounts(
            project_id=self.aigear_config.gcp.gcp_project_id,
            account_name=self.aigear_config.gcp.iam.account_name,
        )
        self.pubsub = PubSub(
            topic_name=self.aigear_config.gcp.pub_sub.topic_name,
            project_id=self.aigear_config.gcp.gcp_project_id,
        )
        self.artifacts = Artifacts(
            repository_name=self.aigear_config.gcp.artifacts.repository_name,
            location=self.aigear_config.gcp.location,
            project_id=self.aigear_config.gcp.gcp_project_id,
        )

    def create(self):
        if self.aigear_config.gcp.iam.on:
            service_accounts_exist = self.service_accounts.describe()
            if not service_accounts_exist:
                logger.info(f"Service accounts({self.aigear_config.gcp.iam.account_name}) not found, will be created.")
                self.service_accounts.create()
                self.service_accounts.add_iam_policy_binding()
            else:
                logger.info(f"Service accounts({self.aigear_config.gcp.iam.account_name}) already exists.")
        else:
            logger.info(f"The service account has been closed in the configuration.")

        if self.aigear_config.gcp.pub_sub.on:
            pubsub_exist = self.pubsub.describe()
            if not pubsub_exist:
                logger.info(f"PubSub({self.aigear_config.gcp.pub_sub.topic_name}) not found, will be created.")
                self.pubsub.create()
            else:
                logger.info(f"PubSub({self.aigear_config.gcp.pub_sub.topic_name}) already exists.")
        else:
            logger.info(f"The pub_sub has been closed in the configuration.")

        if self.aigear_config.gcp.cloud_build.on:
            cloud_build_exist = self.cloud_build.describe()
            if not cloud_build_exist:
                logger.info(
                    f"Cloud build({self.aigear_config.gcp.cloud_build.trigger_name}) not found, will be created.")
                self.cloud_build.create()
            else:
                logger.info(f"Cloud build({self.aigear_config.gcp.cloud_build.trigger_name}) already exists.")
        else:
            logger.info(f"The cloud_build has been closed in the configuration.")

        if self.aigear_config.gcp.bucket.on:
            model_bucket_exist = self.model_bucket.describe()
            if not model_bucket_exist:
                logger.info(
                    f"Model bucket({self.aigear_config.gcp.bucket.bucket_name}) not found, will be created.")
                self.model_bucket.create()
            else:
                logger.info(f"Model bucket({self.aigear_config.gcp.bucket.bucket_name}) already exists.")

            release_model_bucket_exist = self.release_model_bucket.describe()
            if not release_model_bucket_exist:
                logger.info(
                    f"Release model bucket({self.aigear_config.gcp.bucket.bucket_name_for_release}) not found, will be created.")
                self.release_model_bucket.create()
            else:
                logger.info(
                    f"Release model bucket({self.aigear_config.gcp.bucket.bucket_name_for_release}) already exists.")
        else:
            logger.info(f"The bucket has been closed in the configuration.")

        if self.aigear_config.gcp.artifacts.on:
            artifacts_exist = self.artifacts.describe()
            if not artifacts_exist:
                logger.info(
                    f"Artifacts({self.aigear_config.gcp.artifacts.repository_name}) not found, will be created.")
                self.artifacts.create()
            else:
                logger.info(f"Artifacts({self.aigear_config.gcp.pub_sub.topic_name}) already exists.")
        else:
            logger.info(f"The artifacts has been closed in the configuration.")

        if self.aigear_config.gcp.cloud_function.on:
            cloud_function_exist = self.cloud_function.describe()
            if not cloud_function_exist:
                logger.info(
                    f"Cloud function({self.aigear_config.gcp.cloud_function.function_name}) not found, will be created.")
                self.cloud_function.deploy()
            else:
                logger.info(f"Cloud function({self.aigear_config.gcp.cloud_function.function_name}) already exists.")
        else:
            logger.info(f"The cloud_function has been closed in the configuration.")
