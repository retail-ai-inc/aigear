"""
gRPC Service Generator - 使用示例

演示如何使用 aigear 的 gRPC 服务生成器。
"""

from aigear import GrpcServiceGenerator, ModelType, ServiceTemplate
from pathlib import Path


def example_simple_service():
    """示例 1: 简单的单一模型服务"""
    print("=" * 60)
    print("示例 1: 生成简单的分类服务")
    print("=" * 60)

    generator = GrpcServiceGenerator(
        project_name="iris_classifier",
        service_template=ServiceTemplate.SIMPLE,
        model_types=[ModelType.SKLEARN],
        output_dir=Path("./output")
    )

    generator.generate()


def example_multi_version_service():
    """示例 2: 多版本服务（类似 ALC）"""
    print("\n" + "=" * 60)
    print("示例 2: 生成多版本服务（类似 ALC）")
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
    """示例 3: 多公司服务（类似 Macaron）"""
    print("\n" + "=" * 60)
    print("示例 3: 生成多公司服务（类似 Macaron）")
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
    """示例 4: 混合多种模型类型"""
    print("\n" + "=" * 60)
    print("示例 4: 生成混合模型服务")
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
    """示例 5: 推荐系统服务"""
    print("\n" + "=" * 60)
    print("示例 5: 生成推荐系统服务")
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
    print("\n🚀 aigear gRPC Service Generator - 使用示例\n")

    # 运行所有示例
    example_simple_service()
    example_multi_version_service()
    example_multi_company_service()
    example_mixed_models()
    example_recommendation_service()

    print("\n" + "=" * 60)
    print("✨ 所有示例生成完成！")
    print("=" * 60)
    print("\n查看 ./output 目录查看生成的项目")
    print("详细使用文档：docs/GRPC_GENERATOR_GUIDE.md\n")
