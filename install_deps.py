#!/usr/bin/env python3
"""
Aigear dependency automatic installation script
Automatically install required dependencies based on usage scenarios
"""

import subprocess
import sys
import os

def run_pip_install(packages, description=""):
    """Run pip install command"""
    if isinstance(packages, str):
        packages = [packages]

    print(f"\nInstalling {description}: {', '.join(packages)}")

    try:
        cmd = [sys.executable, "-m", "pip", "install"] + packages
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        print(f"✓ Successfully installed: {', '.join(packages)}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"✗ Installation failed: {', '.join(packages)}")
        print(f"Error message: {e.stderr}")
        return False

def install_core_dependencies():
    """Install core required dependencies"""
    print("=" * 60)
    print("Installing core required dependencies")
    print("=" * 60)

    core_deps = [
        ("cloudpickle>=2.0.0", "Object serialization"),
        ("grpcio>=1.54.2", "gRPC communication"),
        ("protobuf>=4.23.3", "Protocol Buffers"),
        ("grpcio-health-checking>=1.56.0", "gRPC health checking"),
    ]

    success_count = 0
    for package, desc in core_deps:
        if run_pip_install(package, desc):
            success_count += 1

    print(f"\nCore dependency installation result: {success_count}/{len(core_deps)} successful")
    return success_count == len(core_deps)

def install_logging_dependencies():
    """Install logging system enhancement dependencies"""
    print("=" * 60)
    print("Installing logging system enhancement dependencies")
    print("=" * 60)

    logging_deps = [
        ("psutil", "System resource monitoring"),
        ("pydantic", "Data validation"),
    ]

    success_count = 0
    for package, desc in logging_deps:
        if run_pip_install(package, desc):
            success_count += 1

    print(f"\nLogging dependency installation result: {success_count}/{len(logging_deps)} successful")
    return success_count >= 1  # Partial success if at least one succeeds

def install_ml_dependencies():
    """Install machine learning related dependencies"""
    print("=" * 60)
    print("Installing machine learning related dependencies")
    print("=" * 60)

    ml_deps = [
        ("scikit-learn", "Machine learning library"),
        ("numpy", "Numerical computing"),
    ]

    success_count = 0
    for package, desc in ml_deps:
        if run_pip_install(package, desc):
            success_count += 1

    print(f"\nML dependency installation result: {success_count}/{len(ml_deps)} successful")
    return success_count >= 1

def install_docker_dependencies():
    """Install Docker related dependencies"""
    print("=" * 60)
    print("Installing Docker related dependencies")
    print("=" * 60)

    # First check if Docker is already installed
    try:
        subprocess.run(["docker", "--version"], check=True, capture_output=True)
        print("✓ Docker is already installed on the system")
        docker_available = True
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("⚠ Docker is not installed on the system, skipping docker-py installation")
        docker_available = False

    if docker_available:
        return run_pip_install("docker>=6.13", "Docker Python client")
    else:
        print("Tip: Please install Docker Desktop (Windows) or docker.io (Linux) first")
        return False

def install_gcp_dependencies():
    """Install GCP related dependencies"""
    print("=" * 60)
    print("Installing GCP cloud service dependencies")
    print("=" * 60)

    gcp_deps = [
        ("google-cloud-logging", "GCP cloud logging"),
        ("google-cloud-build", "GCP build service"),
    ]

    success_count = 0
    for package, desc in gcp_deps:
        if run_pip_install(package, desc):
            success_count += 1

    print(f"\nGCP dependency installation result: {success_count}/{len(gcp_deps)} successful")
    return success_count >= 1

def verify_installation():
    """Verify installation results"""
    print("=" * 60)
    print("Verifying installation results")
    print("=" * 60)

    print("Running dependency check script...")
    try:
        result = subprocess.run([sys.executable, "check_deps.py"],
                              capture_output=True, text=True, cwd=".")
        print("Dependency check completed")
        # Only show key information, avoid encoding issues
        if "COMPLETE" in result.stdout:
            print("✓ Dependency installation verification successful")
        else:
            print("⚠ Some dependencies may need further processing")
        return True
    except Exception as e:
        print(f"Issues occurred during verification: {e}")
        return False

def main():
    """Main installation process"""
    print("Aigear Dependency Automatic Installation Tool")
    print("=" * 60)

    # Show installation options
    print("Available installation options:")
    print("1. Minimal installation (core functionality only)")
    print("2. Recommended installation (core + logging enhancement)")
    print("3. Full installation (includes all features)")
    print("4. Custom installation")
    print("5. Verify current installation only")

    try:
        choice = input("\nPlease select installation type (1-5): ").strip()
    except KeyboardInterrupt:
        print("\nInstallation cancelled")
        return

    if choice == "1":
        # Minimal installation
        print("\nSelection: Minimal installation")
        install_core_dependencies()

    elif choice == "2":
        # Recommended installation
        print("\nSelection: Recommended installation")
        install_core_dependencies()
        install_logging_dependencies()

    elif choice == "3":
        # Full installation
        print("\nSelection: Full installation")
        install_core_dependencies()
        install_logging_dependencies()
        install_ml_dependencies()
        install_docker_dependencies()
        install_gcp_dependencies()

    elif choice == "4":
        # Custom installation
        print("\nSelection: Custom installation")

        components = [
            ("Core dependencies (required)", install_core_dependencies),
            ("Logging enhancement", install_logging_dependencies),
            ("Machine learning", install_ml_dependencies),
            ("Docker support", install_docker_dependencies),
            ("GCP cloud services", install_gcp_dependencies),
        ]

        for name, installer in components:
            try:
                install = input(f"Install {name}? (y/N): ").strip().lower()
                if install in ['y', 'yes']:
                    installer()
            except KeyboardInterrupt:
                print("\nInstallation cancelled")
                return

    elif choice == "5":
        # Verify only
        print("\nSelection: Verify current installation")

    else:
        print("Invalid selection, exiting")
        return

    # Verify installation results
    verify_installation()

    print("\n=" * 60)
    print("Installation completed!")
    print("\nRecommended next steps:")
    print("1. Run 'python test_standalone.py' to test the new logging system")
    print("2. Check 'DEPENDENCY_RESOLUTION.md' for detailed information")
    print("3. Install other optional dependencies based on actual needs")

if __name__ == "__main__":
    main()