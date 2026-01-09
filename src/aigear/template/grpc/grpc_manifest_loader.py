"""
Manifest Loader - Model Manifest Loader

Used to read and parse manifest.json files, automatically discover and load model files.

Features:
- Read manifest.json files
- Validate file format
- Check if required files exist
- Return model file path dictionary
- Support GCS and local file systems
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class ManifestError(Exception):
    """Manifest related errors"""
    pass


class ManifestLoader:
    """Manifest Loader"""

    def __init__(self, base_path: str, manifest_filename: str = "manifest.json"):
        """
        Initialize Manifest Loader

        Args:
            base_path: Model directory path (can be local path or GCS path)
            manifest_filename: Manifest filename (default "manifest.json")
        """
        self.base_path_str = base_path
        self.manifest_filename = manifest_filename
        self.manifest_data = None

        # Detect if it's a GCS path
        self.is_gcs = base_path.startswith('gs://')

        if self.is_gcs:
            # GCS path: keep string format
            self.base_path = base_path.rstrip('/')
            self.manifest_path = f"{self.base_path}/{manifest_filename}"
        else:
            # Local path: use Path object
            self.base_path = Path(base_path)
            self.manifest_path = self.base_path / manifest_filename

    def load(self) -> Dict[str, str]:
        """
        Load manifest and return model file path dictionary

        Returns:
            Model file path dictionary, format: {"model_name": "/path/to/model.pkl"}

        Raises:
            ManifestError: If manifest file does not exist or has invalid format
            FileNotFoundError: If required model files do not exist
        """
        # 1. Read manifest file
        self.manifest_data = self._read_manifest()

        # 2. Validate format
        self._validate_manifest()

        # 3. Build model path dictionary
        model_paths = self._build_model_paths()

        # 4. Verify file existence
        self._verify_files(model_paths)

        logger.info(f"Successfully loaded manifest from {self.manifest_path}")
        logger.info(f"Found {len(model_paths)} model files")

        return model_paths

    def _read_manifest(self) -> Dict:
        """Read manifest.json file (supports local and GCS)"""
        if self.is_gcs:
            # Read from GCS
            return self._read_manifest_from_gcs()
        else:
            # Read from local
            return self._read_manifest_from_local()

    def _read_manifest_from_local(self) -> Dict:
        """Read manifest from local file system"""
        if not self.manifest_path.exists():
            raise ManifestError(
                f"Manifest file not found: {self.manifest_path}\n"
                f"Please ensure manifest.json exists in the model directory."
            )

        try:
            with open(self.manifest_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data
        except json.JSONDecodeError as e:
            raise ManifestError(f"Invalid JSON format in manifest file: {e}")
        except Exception as e:
            raise ManifestError(f"Error reading manifest file: {e}")

    def _read_manifest_from_gcs(self) -> Dict:
        """Read manifest from GCS"""
        try:
            # Use gsutil cat to read GCS file content
            result = subprocess.run(
                ['gsutil', 'cat', self.manifest_path],
                capture_output=True,
                text=True,
                check=True
            )

            # Parse JSON
            data = json.loads(result.stdout)
            logger.info(f"Successfully read manifest from GCS: {self.manifest_path}")
            return data

        except subprocess.CalledProcessError as e:
            raise ManifestError(
                f"Failed to read manifest from GCS: {self.manifest_path}\n"
                f"Error: {e.stderr}\n"
                f"Please ensure gsutil is installed and you have access to the bucket."
            )
        except json.JSONDecodeError as e:
            raise ManifestError(f"Invalid JSON format in GCS manifest file: {e}")
        except Exception as e:
            raise ManifestError(f"Error reading manifest from GCS: {e}")

    def _validate_manifest(self):
        """Validate manifest format"""
        if not isinstance(self.manifest_data, dict):
            raise ManifestError("Manifest must be a JSON object")

        # Check required fields
        if 'models' not in self.manifest_data:
            raise ManifestError("Manifest must contain 'models' field")

        if not isinstance(self.manifest_data['models'], dict):
            raise ManifestError("'models' field must be a dictionary")

        if len(self.manifest_data['models']) == 0:
            raise ManifestError("'models' field cannot be empty")

        # Validate each model entry
        for model_name, model_info in self.manifest_data['models'].items():
            if not isinstance(model_info, dict):
                raise ManifestError(f"Model '{model_name}' info must be a dictionary")

            if 'file' not in model_info:
                raise ManifestError(f"Model '{model_name}' must have 'file' field")

    def _build_model_paths(self) -> Dict[str, str]:
        """Build model file path dictionary (supports local and GCS)"""
        model_paths = {}

        for model_name, model_info in self.manifest_data['models'].items():
            # Get filename
            filename = model_info['file']

            if self.is_gcs:
                # GCS path: return full gs:// path
                full_path = f"{self.base_path}/{filename}"
            else:
                # Local path: use Path object
                full_path = self.base_path / filename
                full_path = str(full_path)

            model_paths[model_name] = full_path

        return model_paths

    def _verify_files(self, model_paths: Dict[str, str]):
        """Verify if model files exist (supports local and GCS)"""
        if self.is_gcs:
            # GCS mode: skip file verification (will verify during download)
            logger.info("GCS mode: Skipping file existence check (will verify during download)")
            return

        # Local mode: verify file existence
        models_info = self.manifest_data['models']

        for model_name, model_path in model_paths.items():
            model_info = models_info[model_name]
            is_required = model_info.get('required', True)

            if not os.path.exists(model_path):
                if is_required:
                    raise FileNotFoundError(
                        f"Required model file not found: {model_path}\n"
                        f"Model: {model_name}\n"
                        f"Please ensure the file exists in the model directory."
                    )
                else:
                    logger.warning(f"Optional model file not found: {model_path} (model: {model_name})")
                    # Remove non-existent optional files from dictionary
                    del model_paths[model_name]

    def get_metadata(self) -> Dict[str, Any]:
        """
        Get metadata from manifest

        Returns:
            Metadata dictionary
        """
        if self.manifest_data is None:
            raise ManifestError("Manifest not loaded yet. Call load() first.")

        return {
            'version': self.manifest_data.get('version'),
            'model_version': self.manifest_data.get('model_version'),
            'created_at': self.manifest_data.get('created_at'),
            'description': self.manifest_data.get('description'),
            'metadata': self.manifest_data.get('metadata', {})
        }

    def get_model_info(self, model_name: str) -> Optional[Dict]:
        """
        Get information for a specific model

        Args:
            model_name: Model name

        Returns:
            Model information dictionary, or None if not found
        """
        if self.manifest_data is None:
            raise ManifestError("Manifest not loaded yet. Call load() first.")

        return self.manifest_data['models'].get(model_name)

    def list_models(self) -> list:
        """
        List all model names

        Returns:
            List of model names
        """
        if self.manifest_data is None:
            raise ManifestError("Manifest not loaded yet. Call load() first.")

        return list(self.manifest_data['models'].keys())


def load_models_from_manifest(base_path: str, manifest_filename: str = "manifest.json") -> Dict[str, str]:
    """
    Convenience function: Load model paths from manifest

    Args:
        base_path: Model directory path
        manifest_filename: Manifest filename

    Returns:
        Model file path dictionary

    Example:
        >>> model_paths = load_models_from_manifest("/models/trial/alc3/")
        >>> print(model_paths)
        {
            "features_min_max_model": "/models/trial/alc3/features_min_max.pkl",
            "scaler_model": "/models/trial/alc3/scaler.pkl",
            "catb_model": "/models/trial/alc3/catb_model.pkl"
        }
    """
    loader = ManifestLoader(base_path, manifest_filename)
    return loader.load()
