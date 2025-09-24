from aigear import aigear_config
from aigear.infrastructure.gcp.bucket import Bucket
from aigear.infrastructure.gcp.build import CloudBuild
from aigear.infrastructure.gcp.function import CloudFunction
from aigear.infrastructure.gcp.iam import ServiceAccounts
from aigear.infrastructure.gcp.pub_sub import PubSub, Subscriptions
from aigear.infrastructure.gcp.constant import (
    entry_point_of_cloud_fuction,
)
from aigear.common.logger import Logging


logger = Logging(log_name=__name__).console_logging()

class Infra:
    def __init__(self):
        self.service_account = f"{aigear_config.gcp.iam.account_name}@{aigear_config.gcp.gcp_project_id}.iam.gserviceaccount.com"
        self.model_bucket = Bucket(
            bucket_name=aigear_config.gcp.bucket.bucket_name,
            location=aigear_config.gcp.bucket.location,
            project_id=aigear_config.gcp.gcp_project_id,
        )
        self.release_model_bucket = Bucket(
            bucket_name=aigear_config.gcp.bucket.bucket_name_for_release,
            location=aigear_config.gcp.bucket.location,
            project_id=aigear_config.gcp.gcp_project_id,
        )
        self.cloud_build = CloudBuild(
            trigger_name=aigear_config.gcp.cloud_build.trigger_name,
            description=aigear_config.gcp.cloud_build.description,
            repo_owner=aigear_config.gcp.cloud_build.repo_owner,
            repo_name=aigear_config.gcp.cloud_build.repo_name,
            branch_pattern=aigear_config.gcp.cloud_build.branch_pattern,
            build_config=aigear_config.gcp.cloud_build.build_config,
            region=aigear_config.gcp.cloud_build.region,
            substitutions=aigear_config.gcp.cloud_build.substitutions,
            project_id=aigear_config.gcp.gcp_project_id,
        )
        self.cloud_function = CloudFunction(
            function_name=aigear_config.gcp.cloud_function.function_name,
            region=aigear_config.gcp.cloud_function.region,
            entry_point=entry_point_of_cloud_fuction,
            topic_name=aigear_config.gcp.pub_sub.topic_name,
            project_id=aigear_config.gcp.gcp_project_id,
            service_account=self.service_account
        )
        self.service_accounts = ServiceAccounts(
            project_id=aigear_config.gcp.gcp_project_id,
            account_name=aigear_config.gcp.iam.account_name,
        )
        self.pubsub = PubSub(
            topic_name=aigear_config.gcp.pub_sub.topic_name,
            project_id=aigear_config.gcp.gcp_project_id,
        )
        self.subscriptions = Subscriptions(
            sub_name=aigear_config.gcp.pub_sub.sub_name,
            topic_name=aigear_config.gcp.pub_sub.topic_name,
            project_id=aigear_config.gcp.gcp_project_id,
        )

    def create(self):
        service_accounts_exist = self.service_accounts.describe()
        if not service_accounts_exist:
            logger.info(f"Service accounts({aigear_config.gcp.iam.account_name}) not found, will be created.")
            self.service_accounts.create()
        else:
            logger.info(f"Service accounts({aigear_config.gcp.iam.account_name}) already exists.")

        pubsub_exist = self.pubsub.describe()
        if not pubsub_exist:
            logger.info(f"PubSub({aigear_config.gcp.pub_sub.topic_name}) not found, will be created.")
            self.pubsub.create()
        else:
            logger.info(f"PubSub({aigear_config.gcp.pub_sub.topic_name}) already exists.")

        subscriptions_exist = self.subscriptions.describe()
        if not subscriptions_exist:
            logger.info(f"Subscriptions({aigear_config.gcp.pub_sub.sub_name}) not found, will be created.")
            self.subscriptions.create()
        else:
            logger.info(f"Subscriptions({aigear_config.gcp.pub_sub.sub_name}) already exists.")

        cloud_build_exist = self.cloud_build.describe()
        if not cloud_build_exist:
            logger.info(f"Cloud build({aigear_config.gcp.cloud_build.trigger_name}) not found, will be created.")
            self.cloud_build.create()
        else:
            logger.info(f"Cloud build({aigear_config.gcp.cloud_build.trigger_name}) already exists.")

        cloud_function_exist = self.cloud_function.describe()
        if not cloud_function_exist:
            logger.info(
                f"Cloud function({aigear_config.gcp.cloud_function.function_name}) not found, will be created.")
            self.cloud_function.deploy()
        else:
            logger.info(f"Cloud function({aigear_config.gcp.cloud_function.function_name}) already exists.")

        model_bucket_exist = self.model_bucket.describe()
        if not model_bucket_exist:
            logger.info(
                f"Model bucket({aigear_config.gcp.bucket.bucket_name}) not found, will be created.")
            self.model_bucket.create()
        else:
            logger.info(f"Model bucket({aigear_config.gcp.bucket.bucket_name}) already exists.")

        release_model_bucket_exist = self.release_model_bucket.describe()
        if not release_model_bucket_exist:
            logger.info(
                f"Release model bucket({aigear_config.gcp.bucket.bucket_name_for_release}) not found, will be created.")
            self.release_model_bucket.create()
        else:
            logger.info(
                f"Release model bucket({aigear_config.gcp.bucket.bucket_name_for_release}) already exists.")
