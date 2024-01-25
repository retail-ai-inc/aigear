from setuptools import find_packages, setup
from pathlib import Path

# read the contents of your README file
this_directory = Path(__file__).parent
long_description = (this_directory / "README.md").read_text()

setup(
    name="aigear",
    author="Retail AI Groups Inc.",
    author_email="",
    version="0.0.1",
    description="Machine learning microservices based on gRPC",
    long_description=long_description,
    url="https://github.com/retail-ai-inc/gear",
    # license="Apache 2.0",
    license_files=["LICENSE"],
    packages=find_packages(),
    include_package_data=True,
    python_requires=">=3.7",
    install_requires=[
        "grpcio >= 1.54.2",
        "protobuf >= 4.23.3",
        "grpcio-health-checking >= 1.56.0",
        "sentry-sdk >= 1.29.2",
    ],
    entry_points={
        "console_scripts": [
            "aigear-microservice = aigear.microservice:main",
        ]
    },
    zip_safe=False,
)
