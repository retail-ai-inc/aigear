"""
GKE Deployment CLI

Command-line interface for deploying gRPC services to GKE.

Usage:
    aigear-deploy-grpc --project-dir /path/to/project --companies trial,aeon --versions alc3,alc4
"""

import argparse
import sys
from pathlib import Path
from aigear.infrastructure.gcp import Infra
import logging


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def deploy_grpc_to_gke():
    """Deploy gRPC service to GKE"""
    parser = argparse.ArgumentParser(
        prog='aigear-deploy-grpc',
        description='Deploy gRPC machine learning service to GKE',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Deploy with auto-detected companies and versions from env.json
  aigear-deploy-grpc --project-dir /path/to/my_alc_service

  # Deploy specific companies and versions
  aigear-deploy-grpc --project-dir . --companies trial,aeon --versions alc3,alc4

  # Use Cloud Build for image building (recommended for production)
  aigear-deploy-grpc --project-dir . --use-cloud-build

  # Deploy to existing cluster (skip cluster creation)
  aigear-deploy-grpc --project-dir . --skip-cluster-creation
        """
    )

    parser.add_argument(
        '--project-dir',
        type=str,
        default='.',
        help='Project directory path (default: current directory)'
    )

    parser.add_argument(
        '--companies',
        type=str,
        help='Comma-separated list of company codes (e.g., trial,aeon). If not provided, will read from env.json'
    )

    parser.add_argument(
        '--versions',
        type=str,
        help='Comma-separated list of versions (e.g., alc3,alc4). If not provided, will read from env.json'
    )

    parser.add_argument(
        '--use-cloud-build',
        action='store_true',
        help='Use Cloud Build for image building (recommended for production)'
    )

    parser.add_argument(
        '--skip-cluster-creation',
        action='store_true',
        help='Skip GKE cluster creation (use existing cluster)'
    )

    args = parser.parse_args()

    # Validate project directory
    project_dir = Path(args.project_dir).resolve()
    if not project_dir.exists():
        logger.error(f"Project directory not found: {project_dir}")
        sys.exit(1)

    # Check for env.json
    env_file = project_dir / "env.json"
    if not env_file.exists():
        logger.error(f"env.json not found in {project_dir}")
        logger.error("Please ensure env.json exists with GKE configuration")
        sys.exit(1)

    # Parse companies and versions
    companies = None
    versions = None

    if args.companies:
        companies = [c.strip() for c in args.companies.split(',')]

    if args.versions:
        versions = [v.strip() for v in args.versions.split(',')]

    logger.info("=" * 60)
    logger.info("GKE Deployment Tool")
    logger.info("=" * 60)
    logger.info(f"Project directory: {project_dir}")
    if companies:
        logger.info(f"Companies: {', '.join(companies)}")
    if versions:
        logger.info(f"Versions: {', '.join(versions)}")
    logger.info(f"Use Cloud Build: {args.use_cloud_build}")
    logger.info("=" * 60)

    try:
        # Create Infra instance
        logger.info("\n📋 Step 1: Initializing GCP infrastructure")
        infra = Infra()

        # Create infrastructure if needed (unless skipping cluster creation)
        if not args.skip_cluster_creation:
            logger.info("\n🏗️  Step 2: Creating GCP infrastructure (if needed)")
            infra.create()
        else:
            logger.info("\n⏭️  Step 2: Skipping infrastructure creation")

        # Deploy gRPC service to GKE
        logger.info("\n🚀 Step 3: Deploying gRPC service to GKE")
        success = infra.deploy_grpc_to_gke(
            project_dir=project_dir,
            companies=companies,
            versions=versions,
            use_cloud_build=args.use_cloud_build,
        )

        if success:
            logger.info("\n" + "=" * 60)
            logger.info("✅ Deployment completed successfully!")
            logger.info("=" * 60)
            logger.info("\n📝 Next steps:")
            logger.info("  1. Check service status: kubectl get services")
            logger.info("  2. Check pod status: kubectl get pods")
            logger.info("  3. View logs: kubectl logs -l app=<your-service-name>")
            logger.info("  4. Get external IP: kubectl get service <service-name>")
            sys.exit(0)
        else:
            logger.error("\n" + "=" * 60)
            logger.error("❌ Deployment failed!")
            logger.error("=" * 60)
            logger.error("\n🔍 Troubleshooting:")
            logger.error("  1. Check GCP credentials: gcloud auth list")
            logger.error("  2. Check project: gcloud config get-value project")
            logger.error("  3. Check env.json configuration")
            logger.error("  4. Check Docker daemon is running")
            sys.exit(1)

    except KeyboardInterrupt:
        logger.info("\n\n⚠️  Deployment interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"\n❌ Deployment failed with error: {str(e)}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    deploy_grpc_to_gke()
