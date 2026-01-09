"""
Configuration Management CLI Tool

Provides command-line functionality for configuration file validation, migration, viewing, etc.
"""

import json
import click
from pathlib import Path

from aigear.common.config_version import (
    get_version_info,
    CURRENT_VERSION,
    MIN_SUPPORTED_VERSION,
    MAX_SUPPORTED_VERSION,
)
from aigear.common.config_validator import (
    validate_config_file,
    get_companies_from_config,
    get_versions_from_config,
)
from aigear.common.config_migrator import migrate_config_file


@click.group()
def config_cli():
    """Configuration file management tool"""
    pass


@config_cli.command()
@click.option(
    "--file",
    "-f",
    default="env.json",
    help="Configuration file path",
    type=click.Path(exists=True),
)
def version(file):
    """View configuration file version information"""
    try:
        with open(file, "r", encoding="utf-8") as f:
            config = json.load(f)

        config_version = config.get("config_version", "Unknown")
        version_info = get_version_info(config_version)

        click.echo(f"\n📋 Configuration file: {file}")
        click.echo(f"📌 Current version: {config_version}")
        click.echo(f"✅ Latest version: {CURRENT_VERSION}")
        click.echo(f"📦 Support range: {MIN_SUPPORTED_VERSION} - {MAX_SUPPORTED_VERSION}")
        click.echo(f"\nStatus:")
        click.echo(f"  - Supported: {'✅ Yes' if version_info['is_supported'] else '❌ No'}")
        click.echo(f"  - Latest: {'✅ Yes' if version_info['is_current'] else '⚠️  No'}")
        click.echo(
            f"  - Needs upgrade: {'⚠️  Yes' if version_info['needs_upgrade'] else '✅ No'}\n"
        )

    except Exception as e:
        click.echo(f"❌ Error: {str(e)}", err=True)
        raise click.Abort()


@config_cli.command()
@click.option(
    "--file",
    "-f",
    default="env.json",
    help="Configuration file path",
    type=click.Path(exists=True),
)
@click.option("--verbose", "-v", is_flag=True, help="Show detailed error information")
def validate(file, verbose):
    """Validate configuration file"""
    click.echo(f"\n🔍 Validating configuration file: {file}\n")

    is_valid, errors = validate_config_file(file)

    if is_valid:
        click.echo("✅ Configuration file validation passed!\n")

        # Display configuration summary
        with open(file, "r", encoding="utf-8") as f:
            config = json.load(f)

        companies = get_companies_from_config(config)
        versions_map = get_versions_from_config(config)

        click.echo("📊 Configuration summary:")
        click.echo(f"  - Number of companies: {len(companies)}")
        click.echo(f"  - Company list: {', '.join(companies)}")
        click.echo(f"  - Version configuration:")

        for company, versions in versions_map.items():
            click.echo(f"    • {company}: {', '.join(versions)}")

        click.echo()

    else:
        click.echo("❌ Configuration file validation failed!\n")
        click.echo("Error list:")

        for i, error in enumerate(errors, 1):
            if verbose:
                click.echo(f"  {i}. {error}")
            else:
                # Simplify error messages
                click.echo(f"  {i}. {error.split(':')[0]}")

        click.echo()
        raise click.Abort()


@config_cli.command()
@click.option(
    "--file",
    "-f",
    default="env.json",
    help="Configuration file path",
    type=click.Path(exists=True),
)
@click.option("--output", "-o", help="Output file path (default: in-place migration)")
@click.option(
    "--target-version", "-t", help=f"Target version (default: {CURRENT_VERSION})"
)
@click.option("--backup/--no-backup", default=True, help="Whether to backup original file")
@click.option("--in-place", is_flag=True, help="In-place migration (overwrite original file)")
def migrate(file, output, target_version, backup, in_place):
    """Migrate configuration file to new version"""
    click.echo(f"\n🔄 Migrating configuration file: {file}\n")

    # Determine output file
    if in_place:
        output_file = file
    elif output:
        output_file = output
    else:
        output_file = None

    # Execute migration
    success = migrate_config_file(file, output_file, target_version, backup)

    if not success:
        raise click.Abort()


@config_cli.command()
@click.option(
    "--file",
    "-f",
    default="env.json",
    help="Configuration file path",
    type=click.Path(exists=True),
)
def info(file):
    """Display detailed configuration file information"""
    try:
        with open(file, "r", encoding="utf-8") as f:
            config = json.load(f)

        click.echo(f"\n📋 Configuration file information: {file}\n")

        # Basic information
        click.echo("Basic information:")
        click.echo(f"  - Project name: {config.get('project_name', 'Not set')}")
        click.echo(f"  - Environment: {config.get('environment', 'Not set')}")
        click.echo(f"  - Config version: {config.get('config_version', 'Unknown')}")
        click.echo(
            f"  - Last updated: {config.get('last_updated', 'Unknown')}"
        )

        # gRPC configuration
        grpc_config = config.get("grpc", {})
        servers = grpc_config.get("servers", {})

        click.echo(f"\ngRPC service configuration:")
        click.echo(f"  - Number of services: {len(servers)}")

        for company, server_config in servers.items():
            port = server_config.get("port", "Not set")
            model_paths = server_config.get("modelPaths", {})
            versions = list(model_paths.keys())

            click.echo(f"\n  📦 {company}:")
            click.echo(f"     - Port: {port}")
            click.echo(f"     - Versions: {', '.join(versions)}")

            for version, path_config in model_paths.items():
                mode = path_config.get("mode", "Unknown")
                base_path = path_config.get("base_path", "Not set")
                click.echo(f"       • {version}: {mode} mode, {base_path}")

        # Deployment configuration
        deployment = grpc_config.get("deployment", {})
        if deployment:
            click.echo(f"\nDeployment configuration:")
            click.echo(f"  - Enabled: {deployment.get('enabled', False)}")
            click.echo(f"  - Preset: {deployment.get('preset', 'Not set')}")

            gke_config = deployment.get("gke", {})
            if gke_config.get("enabled"):
                cluster = gke_config.get("cluster", {})
                click.echo(f"  - GKE cluster: {cluster.get('name', 'Not set')}")
                click.echo(f"  - Location: {cluster.get('location', 'Not set')}")

        click.echo()

    except Exception as e:
        click.echo(f"❌ Error: {str(e)}", err=True)
        raise click.Abort()


if __name__ == "__main__":
    config_cli()
