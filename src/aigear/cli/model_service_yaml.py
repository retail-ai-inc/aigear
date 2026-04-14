import argparse

from aigear.common.config import AppConfig
from aigear.common.constant import ENV_LOCAL, ENV_PRODUCTION, ENV_STAGING
from aigear.deploy.common.helm_chart import create_helm_file

_ALL_ENVS = [ENV_LOCAL, ENV_STAGING, ENV_PRODUCTION]


def get_argument():
    parser = argparse.ArgumentParser(
        description="Generate model service deployment YAML files.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument(
        "--create",
        action="store_true",
        help="Generate model service YAML file(s).",
    )
    parser.add_argument(
        "--version",
        default=None,
        help="Pipeline version to generate YAML for. Omit to generate for all versions.",
    )
    parser.add_argument(
        "--env",
        choices=_ALL_ENVS,
        default=None,
        help="Target environment. Omit to generate for all environments (local, staging, production).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing YAML files.",
    )
    return parser.parse_args()


def _create_yamls(version=None, env=None, force=False):
    envs = [env] if env else _ALL_ENVS
    pipelines = AppConfig.pipelines()

    if version:
        pipeline_items = [(version, pipelines.get(version, {}))]
    else:
        pipeline_items = [(v, c) for v, c in pipelines.items() if isinstance(c, dict)]

    generated = []
    for ver, pipeline_config in pipeline_items:
        ms_config = pipeline_config.get("model_service", {})
        model_class_path = ms_config.get("model_class_path")
        if not model_class_path:
            continue
        for e in envs:
            helm_path = create_helm_file(
                pipeline_version=ver,
                model_class_path=model_class_path,
                venv=ms_config.get("venv_ms"),
                env=e,
                force=force,
            )
            if helm_path:
                print(f"✅ Generated [{ver}][{e}]: {helm_path}")
                generated.append(helm_path)
            else:
                generated.append("")
                print(f"✅ Skipped [{ver}][{e}]: already exists (use --force to overwrite)")
    print("-----------------------------------")

    if not generated:
        print("No YAML files generated (no model_class_path found in env.json).")


def generate_model_service_yaml():
    args = get_argument()
    if args.create:
        _create_yamls(version=args.version, env=args.env, force=args.force)
    else:
        _create_yamls(version=args.version, env=args.env, force=args.force)
