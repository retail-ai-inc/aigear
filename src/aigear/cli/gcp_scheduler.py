import argparse

from aigear.deploy.gcp.scheduler import (
    create_scheduler,
    update_scheduler,
    delete_scheduler,
    status_scheduler,
    list_scheduler,
    run_scheduler,
    pause_scheduler,
    resume_scheduler,
)


def get_argument() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--create", action="store_true", help="Create GCP scheduler job."
    )
    group.add_argument(
        "--update", action="store_true", help="Update an existing GCP scheduler job."
    )
    group.add_argument(
        "--delete", action="store_true", help="Delete a GCP scheduler job."
    )
    group.add_argument(
        "--status", action="store_true", help="Show status of a GCP scheduler job."
    )
    group.add_argument(
        "--list", action="store_true", help="List GCP scheduler jobs filtered by name."
    )
    group.add_argument(
        "--run", action="store_true", help="Manually trigger a GCP scheduler job."
    )
    group.add_argument(
        "--pause", action="store_true", help="Pause a GCP scheduler job."
    )
    group.add_argument(
        "--resume", action="store_true", help="Resume a paused GCP scheduler job."
    )
    parser.add_argument("--version", default="", help="Version of the pipeline.")
    parser.add_argument(
        "--step_names",
        default="",
        help="Comma-separated names of the pipeline steps (required for --create / --update).",
    )
    parser.add_argument(
        "--env",
        default="staging",
        choices=["staging", "production"],
        help="Deployment environment for model service yaml (default: staging).",
    )
    return parser.parse_args()


def gcp_scheduler() -> None:
    args = get_argument()

    if not args.version:
        print("Missing required argument: --version")
        return

    needs_step_names = args.create or args.update
    if needs_step_names and not args.step_names:
        print(
            "Missing required argument: --step_names (required for --create / --update)"
        )
        return

    step_names = args.step_names.split(",") if args.step_names else []

    if args.create:
        create_scheduler(args.version, step_names, args.env)
    elif args.update:
        update_scheduler(args.version, step_names, args.env)
    elif args.delete:
        delete_scheduler(args.version)
    elif args.status:
        status_scheduler(args.version)
    elif args.list:
        list_scheduler(args.version)
    elif args.run:
        run_scheduler(args.version)
    elif args.pause:
        pause_scheduler(args.version)
    elif args.resume:
        resume_scheduler(args.version)
