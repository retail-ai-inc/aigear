"""
配置管理 CLI 工具

提供配置文件的验证、迁移、查看等命令行功能。
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
    """配置文件管理工具"""
    pass


@config_cli.command()
@click.option(
    "--file",
    "-f",
    default="env.json",
    help="配置文件路径",
    type=click.Path(exists=True),
)
def version(file):
    """查看配置文件版本信息"""
    try:
        with open(file, "r", encoding="utf-8") as f:
            config = json.load(f)

        config_version = config.get("config_version", "未知")
        version_info = get_version_info(config_version)

        click.echo(f"\n📋 配置文件: {file}")
        click.echo(f"📌 当前版本: {config_version}")
        click.echo(f"✅ 最新版本: {CURRENT_VERSION}")
        click.echo(f"📦 支持范围: {MIN_SUPPORTED_VERSION} - {MAX_SUPPORTED_VERSION}")
        click.echo(f"\n状态:")
        click.echo(f"  - 是否支持: {'✅ 是' if version_info['is_supported'] else '❌ 否'}")
        click.echo(f"  - 是否最新: {'✅ 是' if version_info['is_current'] else '⚠️  否'}")
        click.echo(
            f"  - 需要升级: {'⚠️  是' if version_info['needs_upgrade'] else '✅ 否'}\n"
        )

    except Exception as e:
        click.echo(f"❌ 错误: {str(e)}", err=True)
        raise click.Abort()


@config_cli.command()
@click.option(
    "--file",
    "-f",
    default="env.json",
    help="配置文件路径",
    type=click.Path(exists=True),
)
@click.option("--verbose", "-v", is_flag=True, help="显示详细错误信息")
def validate(file, verbose):
    """验证配置文件"""
    click.echo(f"\n🔍 正在验证配置文件: {file}\n")

    is_valid, errors = validate_config_file(file)

    if is_valid:
        click.echo("✅ 配置文件验证通过！\n")

        # 显示配置摘要
        with open(file, "r", encoding="utf-8") as f:
            config = json.load(f)

        companies = get_companies_from_config(config)
        versions_map = get_versions_from_config(config)

        click.echo("📊 配置摘要:")
        click.echo(f"  - 公司数量: {len(companies)}")
        click.echo(f"  - 公司列表: {', '.join(companies)}")
        click.echo(f"  - 版本配置:")

        for company, versions in versions_map.items():
            click.echo(f"    • {company}: {', '.join(versions)}")

        click.echo()

    else:
        click.echo("❌ 配置文件验证失败！\n")
        click.echo("错误列表:")

        for i, error in enumerate(errors, 1):
            if verbose:
                click.echo(f"  {i}. {error}")
            else:
                # 简化错误信息
                click.echo(f"  {i}. {error.split(':')[0]}")

        click.echo()
        raise click.Abort()


@config_cli.command()
@click.option(
    "--file",
    "-f",
    default="env.json",
    help="配置文件路径",
    type=click.Path(exists=True),
)
@click.option("--output", "-o", help="输出文件路径（默认为原地迁移）")
@click.option(
    "--target-version", "-t", help=f"目标版本（默认为 {CURRENT_VERSION}）"
)
@click.option("--backup/--no-backup", default=True, help="是否备份原文件")
@click.option("--in-place", is_flag=True, help="原地迁移（覆盖原文件）")
def migrate(file, output, target_version, backup, in_place):
    """迁移配置文件到新版本"""
    click.echo(f"\n🔄 正在迁移配置文件: {file}\n")

    # 确定输出文件
    if in_place:
        output_file = file
    elif output:
        output_file = output
    else:
        output_file = None

    # 执行迁移
    success = migrate_config_file(file, output_file, target_version, backup)

    if not success:
        raise click.Abort()


@config_cli.command()
@click.option(
    "--file",
    "-f",
    default="env.json",
    help="配置文件路径",
    type=click.Path(exists=True),
)
def info(file):
    """显示配置文件详细信息"""
    try:
        with open(file, "r", encoding="utf-8") as f:
            config = json.load(f)

        click.echo(f"\n📋 配置文件信息: {file}\n")

        # 基础信息
        click.echo("基础信息:")
        click.echo(f"  - 项目名称: {config.get('project_name', '未设置')}")
        click.echo(f"  - 环境: {config.get('environment', '未设置')}")
        click.echo(f"  - 配置版本: {config.get('config_version', '未知')}")
        click.echo(
            f"  - 最后更新: {config.get('last_updated', '未知')}"
        )

        # gRPC 配置
        grpc_config = config.get("grpc", {})
        servers = grpc_config.get("servers", {})

        click.echo(f"\ngRPC 服务配置:")
        click.echo(f"  - 服务数量: {len(servers)}")

        for company, server_config in servers.items():
            port = server_config.get("port", "未设置")
            model_paths = server_config.get("modelPaths", {})
            versions = list(model_paths.keys())

            click.echo(f"\n  📦 {company}:")
            click.echo(f"     - 端口: {port}")
            click.echo(f"     - 版本: {', '.join(versions)}")

            for version, path_config in model_paths.items():
                mode = path_config.get("mode", "未知")
                base_path = path_config.get("base_path", "未设置")
                click.echo(f"       • {version}: {mode} mode, {base_path}")

        # 部署配置
        deployment = grpc_config.get("deployment", {})
        if deployment:
            click.echo(f"\n部署配置:")
            click.echo(f"  - 启用: {deployment.get('enabled', False)}")
            click.echo(f"  - 预设: {deployment.get('preset', '未设置')}")

            gke_config = deployment.get("gke", {})
            if gke_config.get("enabled"):
                cluster = gke_config.get("cluster", {})
                click.echo(f"  - GKE 集群: {cluster.get('name', '未设置')}")
                click.echo(f"  - 位置: {cluster.get('location', '未设置')}")

        click.echo()

    except Exception as e:
        click.echo(f"❌ 错误: {str(e)}", err=True)
        raise click.Abort()


if __name__ == "__main__":
    config_cli()
