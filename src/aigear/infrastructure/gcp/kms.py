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
        if "ERROR" in event:
            logger.error(f"Failed to create KMS keyring ({self.keyring_name}): {event}")

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
        elif "ERROR" in event and "NOT_FOUND" not in event:
            logger.error(f"Unexpected error describing keyring ({self.keyring_name}): {event}")
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
        if "ERROR" in event:
            logger.error(f"Failed to create KMS key ({self.key_name}): {event}")

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
        elif "ERROR" in event and "NOT_FOUND" not in event:
            logger.error(f"Unexpected error describing key ({self.key_name}): {event}")
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
        if "Updated IAM policy" in event:
            logger.info("✅ Successfully granted: roles/cloudkms.cryptoKeyEncrypterDecrypter")
        elif "ERROR" in event:
            logger.error(f"❌ Failed to grant KMS permissions: {event}")

    # ------------------------------------------------------------------ #
    #  Encrypt / Decrypt generic files                                     #
    # ------------------------------------------------------------------ #

    def encrypt(
        self, 
        plaintext_file: str | Path, 
        ciphertext_file: str | Path
    ):
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
        if "ERROR" in event:
            logger.error(f"Failed to encrypt file: {event}")

    def decrypt(
        self, 
        ciphertext_file: str | Path, 
        plaintext_file: str | Path
    ):
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
        if "ERROR" in event:
            logger.error(f"Failed to decrypt file: {event}")

    # ------------------------------------------------------------------ #
    #  env.json helpers                                                    #
    # ------------------------------------------------------------------ #

    def encrypt_env(
        self,
        env_path: str | Path = "env.json",
        output_path: str | Path = "staging-env.bin",
    ):
        """Encrypt env.json → env.bin using Cloud KMS."""
        if isinstance(env_path, str):
            env_path = Path(env_path)
        if isinstance(output_path, str):
            output_path = Path(output_path)
        env_path = Path(env_path)
        output_path = Path(output_path)
        if not env_path.exists():
            raise FileNotFoundError(f"env.json not found: {env_path}")
        self.encrypt(plaintext_file=env_path, ciphertext_file=output_path)
        logger.info(f"Encrypted: {env_path} -> {output_path}")

    def decrypt_env(
        self,
        input_path: str | Path = "staging-env.bin",
        output_path: str | Path = "env.json",
    ):
        """Decrypt env.bin → env.json using Cloud KMS."""
        if isinstance(input_path, str):
            input_path = Path(input_path)
        if isinstance(output_path, str):
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
