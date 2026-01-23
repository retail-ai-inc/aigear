"""
gRPC Service Generator CLI - gRPC Service Generator Command Line Tool

Provides an interactive gRPC service generation experience.

Usage:
    aigear-grpc-init
    aigear-grpc-init --name my_service --template multi_company
"""

import argparse
import sys
import logging
from pathlib import Path
from ..generators.grpc_service_generator import GrpcServiceGenerator, ModelType, ServiceTemplate

# Setup logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
cli_logger = logging.getLogger(__name__)


def prompt_user_input(question: str, default: str = None) -> str:
    """Prompt user input"""
    if default:
        answer = input(f"{question} [{default}]: ").strip()
        return answer or default
    else:
        answer = input(f"{question}: ").strip()
        while not answer:
            answer = input(f"{question}: ").strip()
        return answer


def prompt_choice(question: str, choices: list, default: int = 0) -> str:
    """Prompt user choice"""
    print(f"\n{question}")
    for i, choice in enumerate(choices):
        marker = "❯" if i == default else " "
        print(f"  {marker} {i+1}. {choice}")

    while True:
        answer = input(f"\nSelect [1-{len(choices)}] (default: {default+1}): ").strip()
        if not answer:
            return choices[default]
        try:
            index = int(answer) - 1
            if 0 <= index < len(choices):
                return choices[index]
        except ValueError:
            pass
        print(f"Invalid choice, please enter 1-{len(choices)} range")


def prompt_multi_choice(question: str, choices: list) -> list:
    """Prompt user multi-choice"""
    print(f"\n{question}")
    for i, choice in enumerate(choices):
        print(f"  {i+1}. {choice}")

    print("\nEnter option numbers (space-separated, e.g.: 1 2 3）")
    while True:
        answer = input("Select: ").strip()
        if not answer:
            return [choices[0]]

        try:
            indices = [int(x) - 1 for x in answer.split()]
            selected = [choices[i] for i in indices if 0 <= i < len(choices)]
            if selected:
                return selected
        except ValueError:
            pass
        print(f"Invalid choice, please enter a valid number")


def prompt_yes_no(question: str, default: bool = True) -> bool:
    """Prompt Yes/No question"""
    default_str = "Y/n" if default else "y/N"
    answer = input(f"{question} ({default_str}): ").strip().lower()

    if not answer:
        return default

    return answer in ['y', 'yes']


def interactive_mode():
    """Interactive mode"""
    print("\n" + "=" * 60)
    print("🚀 aigear gRPC Service Generator")
    print("=" * 60)

    # 1. Project name
    project_name = prompt_user_input("\n📦 Project name", "my-grpc-service")

    # 2. Select template type
    template_choices = {
        "Simple service (single model)": ServiceTemplate.SIMPLE,
        "Multi-version service": ServiceTemplate.MULTI_VERSION,
        "Multi-company service": ServiceTemplate.MULTI_COMPANY,
    }
    template_name = prompt_choice(
        "🎨 Select service template type:",
        list(template_choices.keys()),
        default=0
    )
    service_template = template_choices[template_name]

    # 3. Select model type
    model_type_choices = {
        "Scikit-learn (joblib)": ModelType.SKLEARN,
        "PyTorch": ModelType.PYTORCH,
        "CatBoost": ModelType.CATBOOST,
        "RankFM (Recommendation algorithm)": ModelType.RANKFM,
        "RecBole (Recommendation framework)": ModelType.RECBOLE,
        "Custom": ModelType.CUSTOM,
    }
    selected_model_names = prompt_multi_choice(
        "🤖 Select model types (multiple):",
        list(model_type_choices.keys())
    )
    model_types = [model_type_choices[name] for name in selected_model_names]

    # 4. Company list (for multi-company template)
    companies = ["demo"]
    if service_template == ServiceTemplate.MULTI_COMPANY:
        companies_input = prompt_user_input(
            "\n🏢 Enter company codes (space-separated)",
            "demo trial aeon"
        )
        companies = companies_input.split()

    # 5. Version list (for multi-version or multi-company templates)
    versions = ["v1"]
    if service_template in [ServiceTemplate.MULTI_VERSION, ServiceTemplate.MULTI_COMPANY]:
        versions_input = prompt_user_input(
            "\n📌 Enter versions (space-separated)",
            "v1 v2"
        )
        versions = versions_input.split()

    # 6. Output directory
    output_dir_str = prompt_user_input("\n📁 Output directory", ".")
    output_dir = Path(output_dir_str)

    # 7. Confirmation
    print("\n" + "=" * 60)
    print("📋 Configuration summary:")
    print("=" * 60)
    print(f"  Project name: {project_name}")
    print(f"  Template type: {template_name}")
    print(f"  Model types: {', '.join([m.value for m in model_types])}")
    print(f"  Companies: {', '.join(companies)}")
    print(f"  Versions: {', '.join(versions)}")
    print(f"  Output directory: {output_dir.absolute()}")
    print("=" * 60)

    if not prompt_yes_no("\nConfirm generation?", default=True):
        print("❌ Cancelled")
        return

    # 8. Generate project
    try:
        generator = GrpcServiceGenerator(
            project_name=project_name,
            service_template=service_template,
            model_types=model_types,
            companies=companies,
            versions=versions,
            output_dir=output_dir
        )
        generator.generate()
        cli_logger.info("\n✨ Project generated successfully！")
    except Exception as e:
        cli_logger.error(f"Generation failed: {str(e)}", exc_info=True)
        sys.exit(1)


def command_line_mode(args):
    """Command line mode"""
    # Parse model types (default to sklearn if not specified)
    model_type_map = {
        "sklearn": ModelType.SKLEARN,
        "pytorch": ModelType.PYTORCH,
        "catboost": ModelType.CATBOOST,
        "rankfm": ModelType.RANKFM,
        "recbole": ModelType.RECBOLE,
        "custom": ModelType.CUSTOM,
    }
    model_types = [model_type_map[m] for m in (args.models or ["sklearn"])]

    # Parse template type (default to simple if not specified)
    template_map = {
        "simple": ServiceTemplate.SIMPLE,
        "multi_version": ServiceTemplate.MULTI_VERSION,
        "multi_company": ServiceTemplate.MULTI_COMPANY,
    }
    service_template = template_map[args.template or "simple"]

    # Generate project
    try:
        generator = GrpcServiceGenerator(
            project_name=args.name,
            service_template=service_template,
            model_types=model_types,
            companies=args.companies,
            versions=args.versions,
            output_dir=Path(args.output)
        )
        generator.generate()

        # Print quick start guide
        print("\n" + "=" * 70)
        print("[SUCCESS] Project generated successfully!")
        print("=" * 70)

        project_path = Path(args.output) / args.name
        print(f"\n[PROJECT] Location: {project_path.absolute()}")

        print("\n[QUICK START GUIDE]")
        print("-" * 70)

        # Step 1: Navigate to project
        print(f"\nStep 1: Navigate to project directory")
        print(f"   cd {args.name}")

        # Step 2: Setup environment
        print(f"\nStep 2: Setup environment (choose one)")
        print(f"   # Option A: Use setup script (recommended)")
        print(f"   ./setup.sh          # Linux/Mac")
        print(f"   setup.bat           # Windows")
        print(f"")
        print(f"   # Option B: Manual setup")
        print(f"   # Review and modify env.json according to your needs")
        print(f"   pip install -r requirements.txt")

        # Step 3: Compile proto files
        print(f"\nStep 3: Compile proto files")
        print(f"   cd service")
        print(f"   python -m grpc_tools.protoc -I../proto --python_out=proto --grpc_python_out=proto ../proto/grpc.proto")

        # Step 4: Run service
        print(f"\nStep 4: Start gRPC service")
        company = args.companies[0]
        version = args.versions[0]
        print(f"   python main.py --company {company} --version {version}")

        # Step 5: Test service
        print(f"\nStep 5: Test service (in another terminal)")
        print(f"   cd {args.name}")
        print(f"   python test_client.py")

        # Additional info
        print("\n" + "-" * 70)
        print("[CONFIGURATION]")
        print(f"   - Service template: {service_template.value}")
        print(f"   - Model types: {', '.join([m.value for m in model_types])}")
        print(f"   - Companies: {', '.join(args.companies)}")
        print(f"   - Versions: {', '.join(args.versions)}")
        print(f"   - Default port: 50051")

        print("\n" + "=" * 70)
        print("Happy coding!")
        print("=" * 70 + "\n")

    except Exception as e:
        cli_logger.error(f"Generation failed: {str(e)}", exc_info=True)
        sys.exit(1)


def main():
    """Main function"""
    parser = argparse.ArgumentParser(
        description="aigear gRPC Service Generator - Generate gRPC machine learning service projects",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Interactive mode
  aigear-grpc-init

  # Command line mode
  aigear-grpc-init --name my_service --template simple --models sklearn

  # Multi-company service
  aigear-grpc-init \\
    --name macaron \\
    --template multi_company \\
    --models rankfm pytorch \\
    --companies trial aeon \\
    --versions v1 v2
        """
    )

    parser.add_argument(
        "--name",
        type=str,
        help="Project name"
    )

    parser.add_argument(
        "--template",
        type=str,
        choices=["simple", "multi_version", "multi_company"],
        help="Service template type"
    )

    parser.add_argument(
        "--models",
        type=str,
        nargs="+",
        choices=["sklearn", "pytorch", "catboost", "rankfm", "recbole", "custom"],
        help="Model type list"
    )

    parser.add_argument(
        "--companies",
        type=str,
        nargs="+",
        default=["demo"],
        help="Company code list (for multi_company template)"
    )

    parser.add_argument(
        "--versions",
        type=str,
        nargs="+",
        default=["v1"],
        help="Version list (for multi_version and multi_company templates)"
    )

    parser.add_argument(
        "--output",
        type=str,
        default=".",
        help="Output directory (defaults to current directory)"
    )

    args = parser.parse_args()

    # Determine interactive or command line mode
    # If only --name is provided, use command line mode with defaults
    if args.name:
        command_line_mode(args)
    else:
        interactive_mode()


if __name__ == "__main__":
    main()
