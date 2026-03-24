from pathlib import Path

from aigear.common import run_sh
from aigear.common.logger import Logging

logger = Logging(log_name=__name__).console_logging()



class CloudKMS:
    def __init__(
        self,
        project_id: str,
        location: str,
        keyring_name: str,
        key_name: str,
    ):
        self.project_id = project_id
        self.location = location
        self.keyring_name = keyring_name
        self.key_name = key_name

    # ------------------------------------------------------------------ #
    #  Key ring                                                            #
    # ------------------------------------------------------------------ #

    def create_keyring(self):
        command = [
            "gcloud", "kms", "keyrings", "create",
            self.keyring_name,
            f"--location={self.location}",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        logger.info(event)

    def describe_keyring(self) -> bool:
        is_exist = False
        command = [
            "gcloud", "kms", "keyrings", "describe",
            self.keyring_name,
            f"--location={self.location}",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        if self.keyring_name in event and "ERROR" not in event:
            is_exist = True
            logger.info(f"Find keyring: {event}")
        elif "NOT_FOUND" in event:
            logger.info(f"NOT_FOUND: Keyring not found (keyring={self.keyring_name})")
        else:
            logger.info(event)
        return is_exist

    # ------------------------------------------------------------------ #
    #  Encryption key                                                      #
    # ------------------------------------------------------------------ #

    def create_key(self):
        command = [
            "gcloud", "kms", "keys", "create",
            self.key_name,
            f"--keyring={self.keyring_name}",
            f"--location={self.location}",
            "--purpose=encryption",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        logger.info(event)

    def describe_key(self) -> bool:
        is_exist = False
        command = [
            "gcloud", "kms", "keys", "describe",
            self.key_name,
            f"--keyring={self.keyring_name}",
            f"--location={self.location}",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        if self.key_name in event and "ERROR" not in event:
            is_exist = True
            logger.info(f"Find key: {event}")
        elif "NOT_FOUND" in event:
            logger.info(f"NOT_FOUND: Key not found (key={self.key_name})")
        else:
            logger.info(event)
        return is_exist

    # ------------------------------------------------------------------ #
    #  IAM                                                                 #
    # ------------------------------------------------------------------ #

    def add_permissions(self, sa_email: str):
        command = [
            "gcloud", "kms", "keys", "add-iam-policy-binding",
            self.key_name,
            f"--keyring={self.keyring_name}",
            f"--location={self.location}",
            f"--member=serviceAccount:{sa_email}",
            "--role=roles/cloudkms.cryptoKeyEncrypterDecrypter",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        logger.info(event)

    # ------------------------------------------------------------------ #
    #  Encrypt / Decrypt generic files                                     #
    # ------------------------------------------------------------------ #

    def encrypt(self, plaintext_file: str | Path, ciphertext_file: str | Path):
        command = [
            "gcloud", "kms", "encrypt",
            f"--key={self.key_name}",
            f"--keyring={self.keyring_name}",
            f"--location={self.location}",
            f"--plaintext-file={plaintext_file}",
            f"--ciphertext-file={ciphertext_file}",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        logger.info(event)

    def decrypt(self, ciphertext_file: str | Path, plaintext_file: str | Path):
        command = [
            "gcloud", "kms", "decrypt",
            f"--key={self.key_name}",
            f"--keyring={self.keyring_name}",
            f"--location={self.location}",
            f"--ciphertext-file={ciphertext_file}",
            f"--plaintext-file={plaintext_file}",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        logger.info(event)

    # ------------------------------------------------------------------ #
    #  env.json helpers                                                    #
    # ------------------------------------------------------------------ #

    def encrypt_env(
        self,
        env_path: str | Path,
        output_path: str | Path,
    ):
        """Encrypt env.json → env.json.enc using Cloud KMS."""
        env_path = Path(env_path)
        output_path = Path(output_path)
        if not env_path.exists():
            raise FileNotFoundError(f"env.json not found: {env_path}")
        self.encrypt(plaintext_file=env_path, ciphertext_file=output_path)
        logger.info(f"Encrypted: {env_path} -> {output_path}")

    def decrypt_env(
        self,
        input_path: str | Path,
        output_path: str | Path,
    ):
        """Decrypt env.json.enc → env.json using Cloud KMS."""
        input_path = Path(input_path)
        output_path = Path(output_path)
        if not input_path.exists():
            raise FileNotFoundError(f"Encrypted env file not found: {input_path}")
        self.decrypt(ciphertext_file=input_path, plaintext_file=output_path)
        logger.info(f"Decrypted: {input_path} -> {output_path}")


if __name__ == "__main__":
    project_id = ""
    location = ""
    keyring_name = ""
    key_name = ""
    ENV_JSON = "env.json"
    ENV_JSON_ENC = "local-env.bin"

    cloud_kms = CloudKMS(
        project_id=project_id,
        location=location,
        keyring_name=keyring_name,
        key_name=key_name,
    )

    # Create resources (idempotent — skip if already exist)
    if not cloud_kms.describe_keyring():
        cloud_kms.create_keyring()
    if not cloud_kms.describe_key():
        cloud_kms.create_key()

    # Encrypt env.json
    cloud_kms.encrypt_env()

    # Decrypt env.json.enc back to env.json
    # kms.decrypt_env()
