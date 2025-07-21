from google.cloud import secretmanager_v1


class SecretManager:
    def __init__(self, project_id):
        self.project_id = project_id

    def get_secret_val(
        self,
        secret_id: str,
        secret_version: str = "latest",
        secret_decoding: str="utf-8",
    ) -> str:
        sm_client = (secretmanager_v1.sm_client) = secretmanager_v1.SecretManagerServiceClient()
        secret_name = sm_client.secret_version_path(self.project_id, secret_id, secret_version)
        result = sm_client.access_secret_version(name=secret_name)

        return result.payload.data.decode(secret_decoding)
