import argparse
from pathlib import Path

from aigear.common.config import AigearConfig
from aigear.infrastructure.gcp.kms import CloudKMS


def get_argument() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Encrypt or decrypt env.json using Cloud KMS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--encrypt",
        action="store_true",
        help="Encrypt env.json to a .bin ciphertext file.",
    )
    group.add_argument(
        "--decrypt",
        action="store_true",
        help="Decrypt a .bin ciphertext file to env.json.",
    )
    parser.add_argument(
        "--environment",
        default="staging",
        choices=["staging", "production"],
        help="Target environment. Determines default ciphertext path: kms/<env>/<env>-env.bin (default: staging).",
    )
    parser.add_argument(
        "--input",
        default=None,
        help="Override input file path.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Override output file path.",
    )
    # KMS config overrides — required when env.json is not yet available (e.g. --decrypt)
    parser.add_argument("--project-id", default=None, help="GCP project ID.")
    parser.add_argument(
        "--location", default=None, help="KMS location (e.g. asia-northeast1)."
    )
    parser.add_argument("--keyring", default=None, help="KMS keyring name.")
    parser.add_argument("--key", default=None, help="KMS key name.")
    return parser.parse_args()


def _build_kms(args: argparse.Namespace) -> CloudKMS:
    """
    Resolve KMS config from CLI args first, falling back to env.json.
    When decrypting, env.json may not yet exist, so CLI args are required.
    """
    if args.project_id and args.location and args.keyring and args.key:
        return CloudKMS(
            project_id=args.project_id,
            location=args.location,
            keyring_name=args.keyring,
            key_name=args.key,
        )

    try:
        gcp = AigearConfig.get_config().gcp
        return CloudKMS(
            project_id=args.project_id or gcp.gcp_project_id,
            location=args.location or gcp.location,
            keyring_name=args.keyring or gcp.kms.keyring_name,
            key_name=args.key or gcp.kms.key_name,
        )
    except FileNotFoundError:
        raise SystemExit(
            "env.json not found. When decrypting, provide KMS config via:\n"
            "  --project-id PROJECT --location LOCATION --keyring KEYRING --key KEY"
        )


def kms_env() -> None:
    args = get_argument()
    cloud_kms = _build_kms(args)

    env = args.environment
    project_root = Path.cwd()
    default_env_path = project_root / "env.json"
    default_bin_path = project_root / "kms" / f"{env}" / f"{env}-env.bin"

    if args.encrypt:
        input_path = args.input or default_env_path
        output_path = Path(args.output) if args.output else default_bin_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cloud_kms.encrypt_env(env_path=input_path, output_path=output_path)
    else:
        input_path = args.input or default_bin_path
        output_path = args.output or default_env_path
        cloud_kms.decrypt_env(input_path=input_path, output_path=output_path)
