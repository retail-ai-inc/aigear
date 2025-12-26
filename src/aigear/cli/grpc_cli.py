"""
aigear-grpc CLI - gRPC Service Generation and Management Tool

Provides command-line tools for generating and managing gRPC machine learning service projects.

Usage:
    # Create gRPC service for a single pipeline
    aigear-grpc create --name my_service --pipeline pipeline_v1
"""

import argparse
import sys
from pathlib import Path
from typing import List, Optional
from ..generators import GrpcServiceGenerator, ModelType, ServiceTemplate
import logging

# Use simple logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ==================== Command Handler Functions ====================

def create_project(args):
    """Create new gRPC service project"""
    try:
        # Validate required parameters
        if not args.pipeline:
            logger.error("--pipeline is required")
            sys.exit(1)

        # Parse model types
        model_type_map = {
            "sklearn": ModelType.SKLEARN,
            "pytorch": ModelType.PYTORCH,
            "catboost": ModelType.CATBOOST,
            "rankfm": ModelType.RANKFM,
            "recbole": ModelType.RECBOLE,
            "custom": ModelType.CUSTOM,
        }
        model_types = [model_type_map[m] for m in (args.models or ['sklearn'])]

        # Pipeline-centric configuration
        config = {
            'project_name': args.name,
            'service_template': ServiceTemplate.SIMPLE,  # Single pipeline = simple template
            'model_types': model_types,
            'pipeline': args.pipeline,
            'features': {
                'sentry': True,
                'health_check': True,
                'keepalive': True,
                'multi_processing': True,
                'max_message_size': 52428800,
            }
        }

        # Output directory
        output_dir = Path(args.output) if args.output else Path.cwd()

        # Generate project
        logger.info(f"Generating gRPC service: {config['project_name']}")
        logger.info(f"Pipeline: {args.pipeline}")
        logger.info(f"Output directory: {output_dir.absolute()}")

        # Note: GrpcServiceGenerator needs to be updated to support pipeline-centric mode
        # For now, we use companies=['demo'] and versions=['v1'] as placeholders
        generator = GrpcServiceGenerator(
            project_name=config['project_name'],
            service_template=config['service_template'],
            model_types=config['model_types'],
            companies=[args.pipeline],  # Use pipeline name as company
            versions=['v1'],  # Single version
            output_dir=output_dir,
            features=config.get('features', {})
        )

        generator.generate()

        logger.info("\n✨ gRPC service generated successfully!")
        logger.info(f"📁 Project path: {output_dir / config['project_name']}")

    except Exception as e:
        logger.error(f"Generation failed: {str(e)}", exc_info=True)
        sys.exit(1)


# ==================== Main Function ====================

def main():
    """Main function"""
    parser = argparse.ArgumentParser(
        prog='aigear-grpc',
        description="aigear-grpc - gRPC Machine learning service generation tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create gRPC service for a pipeline
  aigear-grpc create --name my_service --pipeline pipeline_v1

  # Create with specific model types
  aigear-grpc create --name my_service --pipeline pipeline_v1 --models sklearn catboost
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # ==================== create command ====================
    create_parser = subparsers.add_parser(
        'create',
        help='Create new gRPC service project',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    create_parser.add_argument(
        '--name',
        type=str,
        required=True,
        help='Project name'
    )

    create_parser.add_argument(
        '--pipeline',
        type=str,
        required=True,
        help='Pipeline name (e.g., pipeline_v1)'
    )

    create_parser.add_argument(
        '--models',
        type=str,
        nargs='+',
        choices=['sklearn', 'pytorch', 'catboost', 'rankfm', 'recbole', 'custom'],
        help='Model type list (default: sklearn)'
    )

    create_parser.add_argument(
        '--output',
        type=str,
        help='Output directory (defaults to current directory)'
    )

    # Parse arguments
    args = parser.parse_args()

    # Execute command
    if args.command == 'create':
        create_project(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
