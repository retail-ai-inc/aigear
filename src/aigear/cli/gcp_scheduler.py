import argparse

from aigear.deploy.gcp.scheduler import create_scheduler


def get_argument():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument(
        "--create",
        action="store_true",
        help="Create GCP scheduler job."
    )
    # Future commands:
    # group.add_argument("--delete", action="store_true", help="...")
    # group.add_argument("--update", action="store_true", help="...")
    parser.add_argument("--version", default="",
                        help="Version of the pipeline.")
    parser.add_argument("--step_names", default="",
                        help="Comma-separated names of the pipeline steps.")
    return parser.parse_args()


def gcp_scheduler():
    args = get_argument()

    missing = []
    if not args.version:
        missing.append("--version")
    if not args.step_names:
        missing.append("--step_names")
    if missing:
        print(f"Missing required argument(s): {', '.join(missing)}")
        return

    step_names = args.step_names.split(",")
    if args.create:
        create_scheduler(args.version, step_names)
    # Future commands:
    # elif args.delete:
    #     delete_scheduler(args.version, step_names)
    # elif args.update:
    #     update_scheduler(args.version, step_names)
    else:
        create_scheduler(args.version, step_names)
