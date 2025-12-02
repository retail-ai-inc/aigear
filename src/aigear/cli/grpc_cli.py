"""
aigear-grpc CLI - gRPC Service Generation and Management Tool

Provides command-line tools for generating and managing gRPC machine learning service projects.

Usage:
    # Create new project (ALC preset)
    aigear-grpc create --name my_alc --preset alc --companies trial,aeon --versions alc3,alc4

    # Create new project (Macaron preset)
    aigear-grpc create --name my_macaron --preset macaron --companies trial,tec --versions ape3,ape4

    # Create custom project
    aigear-grpc create --name my_service --companies trial --versions v1
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


# ==================== Preset Configurations ====================

class ProjectPreset:
    """Project preset configurations"""

    @staticmethod
    def get_alc_preset(project_name: str, companies: List[str], versions: List[str]) -> dict:
        """ALC preset configuration

        Features:
        - Multi-company, multi-version
        - Classification models (scikit-learn + CatBoost)
        - modelPaths configuration (multiple model files)
        - Sentry + Health Check
        - Multi-processing support
        """
        return {
            'project_name': project_name,
            'service_template': ServiceTemplate.MULTI_COMPANY,
            'model_types': [ModelType.SKLEARN, ModelType.CATBOOST],
            'companies': companies or ['trial', 'aeon'],
            'versions': versions or ['alc3', 'alc4'],
            'features': {
                'sentry': True,
                'health_check': True,
                'keepalive': True,
                'multi_processing': True,
                'max_message_size': 52428800,  # 50MB
                'base_class': 'BaseClassifier',
            },
            'model_files': {
                'alc3': [
                    'features_min_max_model',
                    'scaler_model',
                    'rfc_model',
                    'catb_model',
                    'default_threshold',
                    'thresholds',
                ],
                'alc4': [
                    'features_min_max_model',
                    'regressor_model',
                ]
            }
        }

    @staticmethod
    def get_macaron_preset(project_name: str, companies: List[str], versions: List[str]) -> dict:
        """Macaron preset configuration

        Features:
        - Multi-company, multi-version
        - Recommendation system (RankFM)
        - modelPaths configuration
        - Sentry + Health Check + KeepAlive
        - Base class template (BaseRecommender)
        """
        return {
            'project_name': project_name,
            'service_template': ServiceTemplate.MULTI_COMPANY,
            'model_types': [ModelType.RANKFM],
            'companies': companies or ['trial', 'tec', 'demo'],
            'versions': versions or ['ape3', 'ape4'],
            'features': {
                'sentry': True,
                'health_check': True,
                'keepalive': True,
                'multi_processing': True,
                'max_message_size': 52428800,
                'base_class': 'BaseRecommender',
                'template_variables': True,  # Support ${subsidiaryName}
            },
            'model_files': {
                'default': ['model_file']
            }
        }


# ={20} Command Handler Functions ={20}

def create_project(args):
    """Create new project"""
    try:
        # Parse companies and versions list
        companies = args.companies.split(',') if args.companies else []
        versions = args.versions.split(',') if args.versions else []

        # Based on preset or custom configuration
        if args.preset:
            if args.preset == 'alc':
                config = ProjectPreset.get_alc_preset(args.name, companies, versions)
            elif args.preset == 'macaron':
                config = ProjectPreset.get_macaron_preset(args.name, companies, versions)
            else:
                logger.error(f"Unknown preset: {args.preset}")
                sys.exit(1)

            logger.info(f"Using preset: {args.preset}")
            logger.info(f"Companies: {', '.join(config['companies'])}")
            logger.info(f"Versions: {', '.join(config['versions'])}")
        else:
            # Custom configuration
            if not companies or not versions:
                logger.error("Custom mode requires --companies and --versions")
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

            config = {
                'project_name': args.name,
                'service_template': ServiceTemplate.MULTI_COMPANY,
                'model_types': model_types,
                'companies': companies,
                'versions': versions,
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
        logger.info(f"Generating project: {config['project_name']}")
        logger.info(f"Output directory: {output_dir.absolute()}")

        generator = GrpcServiceGenerator(
            project_name=config['project_name'],
            service_template=config['service_template'],
            model_types=config['model_types'],
            companies=config['companies'],
            versions=config['versions'],
            output_dir=output_dir,
            features=config.get('features', {}),
            model_files=config.get('model_files', {})
        )

        generator.generate()

        logger.info("\n✨ Project generated successfully！")
        logger.info(f"📁 Project path: {output_dir / config['project_name']}")

    except Exception as e:
        logger.error(f"Generation failed: {str(e)}", exc_info=True)
        sys.exit(1)


# ={20} Main Function ={20}

def main():
    """Main function"""
    parser = argparse.ArgumentParser(
        prog='aigear-grpc',
        description="aigear-grpc - gRPC Machine learning service generation and management tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create project using ALC preset
  aigear-grpc create --name my_alc --preset alc

  # Use ALC preset, specify companies and versions
  aigear-grpc create --name my_alc --preset alc --companies trial,aeon --versions alc3,alc4

  # Use Macaron preset
  aigear-grpc create --name my_macaron --preset macaron --companies trial,tec --versions ape3,ape4

  # Custom project
  aigear-grpc create --name my_service --companies trial,aeon --versions v1,v2 --models sklearn catboost
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # ={20} create command ={20}
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
        '--preset',
        type=str,
        choices=['alc', 'macaron'],
        help='Use preset configuration (alc or macaron)'
    )

    create_parser.add_argument(
        '--companies',
        type=str,
        help='Company code list (comma-separated, e.g.: trial,aeon,tec)'
    )

    create_parser.add_argument(
        '--versions',
        type=str,
        help='Versions (comma-separated, e.g.: v1,v2 or alc3,alc4)'
    )

    create_parser.add_argument(
        '--models',
        type=str,
        nargs='+',
        choices=['sklearn', 'pytorch', 'catboost', 'rankfm', 'recbole', 'custom'],
        help='Model type list (only required for custom mode)'
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
