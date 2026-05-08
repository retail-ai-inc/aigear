from __future__ import annotations

import json
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
        run_sh(command, check=True)

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
        run_sh(command, check=True)

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

    def describe_enabled_key_version(self) -> bool:
        command = [
            "gcloud", "kms", "keys", "versions", "list",
            f"--key={self.key_name}",
            f"--keyring={self.keyring_name}",
            f"--location={self.location}",
            f"--project={self.project_id}",
            "--filter=state=ENABLED",
            "--format=json",
        ]
        output = run_sh(command)
        try:
            versions = json.loads(output)
            return len(versions) > 0
        except Exception:
            return False

    def enable_primary_key_version(self):
        """Restore and enable the primary key version."""
        describe_cmd = [
            "gcloud", "kms", "keys", "describe",
            self.key_name,
            f"--keyring={self.keyring_name}",
            f"--location={self.location}",
            f"--project={self.project_id}",
            "--format=json",
        ]
        output = run_sh(describe_cmd)
        try:
            key_info = json.loads(output)
        except Exception:
            raise RuntimeError(f"Failed to describe KMS key ({self.key_name}).")

        primary = key_info.get("primary")
        if not primary:
            raise RuntimeError(
                f"No primary version found for key ({self.key_name})."
            )

        version_num = primary["name"].split("/")[-1]
        state = primary.get("state", "")

        if state == "ENABLED":
            logger.info(f"KMS key primary version {version_num} is already ENABLED.")
            return

        if state == "DESTROYED":
            raise RuntimeError(
                f"KMS key primary version {version_num} is already DESTROYED and cannot be recovered."
            )

        if state not in ("DISABLED", "DESTROY_SCHEDULED"):
            raise RuntimeError(
                f"KMS key primary version {version_num} is in unexpected state: {state}."
            )

        if state == "DESTROY_SCHEDULED":
            restore_cmd = [
                "gcloud", "kms", "keys", "versions", "restore",
                version_num,
                f"--key={self.key_name}",
                f"--keyring={self.keyring_name}",
                f"--location={self.location}",
                f"--project={self.project_id}",
            ]
            run_sh(restore_cmd, check=True)
            logger.info(f"KMS primary key version {version_num} restored from DESTROY_SCHEDULED to DISABLED.")

        enable_cmd = [
            "gcloud", "kms", "keys", "versions", "enable",
            version_num,
            f"--key={self.key_name}",
            f"--keyring={self.keyring_name}",
            f"--location={self.location}",
            f"--project={self.project_id}",
        ]
        run_sh(enable_cmd, check=True)
        logger.info(f"KMS primary key version {version_num} enabled successfully.")

    # ------------------------------------------------------------------ #
    #  IAM                                                                 #
    # ------------------------------------------------------------------ #

    def delete(self):
        list_cmd = [
            "gcloud", "kms", "keys", "versions", "list",
            f"--key={self.key_name}",
            f"--keyring={self.keyring_name}",
            f"--location={self.location}",
            f"--project={self.project_id}",
            "--format=json",
        ]
        output = run_sh(list_cmd)
        try:
            versions = json.loads(output)
        except Exception:
            versions = []

        destroyed = 0
        for v in versions:
            state = v.get("state", "")
            version_num = v.get("name", "").split("/")[-1]
            if state in ("ENABLED", "DISABLED") and version_num:
                destroy_cmd = [
                    "gcloud", "kms", "keys", "versions", "destroy",
                    version_num,
                    f"--key={self.key_name}",
                    f"--keyring={self.keyring_name}",
                    f"--location={self.location}",
                    f"--project={self.project_id}",
                ]
                event = run_sh(destroy_cmd)
                if "ERROR" in event:
                    logger.error(f"Failed to destroy key version {version_num}: {event}")
                else:
                    destroyed += 1

        logger.warning(
            f"KMS keyring ({self.keyring_name}) cannot be deleted — GCP does not support keyring deletion. "
            f"Scheduled {destroyed} key version(s) for destruction."
        )

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
        run_sh(command, check=True)
        logger.info("✅ Successfully granted: roles/cloudkms.cryptoKeyEncrypterDecrypter")

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
        run_sh(command, check=True)

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
        run_sh(command, check=True)

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

