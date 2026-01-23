"""
gRPC Service Generator - Usage Examples

Demonstrates how to use aigear's gRPC service generator.
"""

from aigear import GrpcServiceGenerator, ModelType, ServiceTemplate
from pathlib import Path


def example_simple_service():
    """Example 1: Simple single model service"""
    print("=" * 60)
    print("Example 1: Generate simple classification service")
    print("=" * 60)

    generator = GrpcServiceGenerator(
        project_name="iris_classifier",
        service_template=ServiceTemplate.SIMPLE,
        model_types=[ModelType.SKLEARN],
        output_dir=Path("./output")
    )

    generator.generate()


def example_multi_version_service():
    """Example 2: Multi-version service (similar to ALC)"""
    print("\n" + "=" * 60)
    print("Example 2: Generate multi-version service (similar to ALC)")
    print("=" * 60)

    generator = GrpcServiceGenerator(
        project_name="alc_service",
        service_template=ServiceTemplate.MULTI_VERSION,
        model_types=[ModelType.SKLEARN, ModelType.CATBOOST],
        versions=["alc3", "alc4"],
        output_dir=Path("./output")
    )

    generator.generate()


def example_multi_company_service():
    """Example 3: Multi-company service (similar to Macaron)"""
    print("\n" + "=" * 60)
    print("Example 3: Generate multi-company service (similar to Macaron)")
    print("=" * 60)

    generator = GrpcServiceGenerator(
        project_name="macaron_service",
        service_template=ServiceTemplate.MULTI_COMPANY,
        model_types=[ModelType.RANKFM, ModelType.PYTORCH],
        companies=["trial", "aeon", "tec"],
        versions=["ape3", "ape4"],
        output_dir=Path("./output")
    )

    generator.generate()


def example_mixed_models():
    """Example 4: Mixed multiple model types"""
    print("\n" + "=" * 60)
    print("Example 4: Generate mixed model service")
    print("=" * 60)

    generator = GrpcServiceGenerator(
        project_name="ensemble_service",
        service_template=ServiceTemplate.MULTI_VERSION,
        model_types=[
            ModelType.SKLEARN,
            ModelType.PYTORCH,
            ModelType.CATBOOST
        ],
        versions=["ensemble_v1", "ensemble_v2"],
        output_dir=Path("./output")
    )

    generator.generate()


def example_recommendation_service():
    """Example 5: Recommendation system service"""
    print("\n" + "=" * 60)
    print("Example 5: Generate recommendation system service")
    print("=" * 60)

    generator = GrpcServiceGenerator(
        project_name="recommendation_service",
        service_template=ServiceTemplate.MULTI_COMPANY,
        model_types=[ModelType.RANKFM, ModelType.RECBOLE],
        companies=["retail_a", "retail_b"],
        versions=["collaborative_filtering", "deep_learning"],
        output_dir=Path("./output")
    )

    generator.generate()


if __name__ == "__main__":
    print("\n🚀 aigear gRPC Service Generator - Usage Examples\n")

    # Run all examples
    example_simple_service()
    example_multi_version_service()
    example_multi_company_service()
    example_mixed_models()
    example_recommendation_service()

    print("\n" + "=" * 60)
    print("✨ All examples generated successfully!")
    print("=" * 60)
    print("\nCheck ./output directory to view generated projects")
    print("Detailed documentation: docs/GRPC_GENERATOR_GUIDE.md\n")
