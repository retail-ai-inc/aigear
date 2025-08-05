from aigear import aigear_logger
from aigear.common import run_sh


class CloudBuild:
    def __init__(
        self,
        trigger_name: str,
        description: str,
        repo_owner: str,
        repo_name: str,
        branch_pattern: str,
        build_config: str,
        region: str,
        substitutions: str,
        project_id: str,
    ):
        self.trigger_name = trigger_name
        self.description = description
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.branch_pattern = branch_pattern
        self.build_config = build_config
        self.region = region
        self.substitutions = substitutions
        self.project_id = project_id

    def create(self):
        command = [
            "gcloud", "builds", "triggers", "create", "github",
            f"--name={self.trigger_name}",
            f"--description={self.description}",
            f"--repo-owner={self.repo_owner}",
            f"--repo-name={self.repo_name}",
            f"--branch-pattern={self.branch_pattern}",
            f"--build-config={self.build_config}",
            f"--region={self.region}",
            f"--substitutions={self.substitutions}",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        aigear_logger.info(event)


if __name__ == "__main__":
    trigger_name = "ml-test"
    description = "The pipelines of Testing"
    repository = "retail-ai-inc/medovik"
    repo_owner = "retail-ai-inc"
    repo_name = "medovik"
    branch_pattern = "^master$"
    build_config = "/cloudbuild/cloudbuild.yaml"
    region = "global"
    project_id = "ssc-ape-staging"
    substitutions = "_ENVIRONMENT=staging,_KMS_LOCATION=asia-northeast1,_PIPELINES_VERSION=ape3,_PLATFORM=medovik"
    cloud_build = CloudBuild(
        trigger_name=trigger_name,
        description=description,
        repo_owner=repo_owner,
        repo_name=repo_name,
        branch_pattern=branch_pattern,
        build_config=build_config,
        region=region,
        substitutions=substitutions,
        project_id=project_id,
    )
    cloud_build.create()
