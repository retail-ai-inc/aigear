"""
Manifest Loader - 模型清单加载器

用于读取和解析 manifest.json 文件，自动发现和加载模型文件。

Features:
- 读取 manifest.json 文件
- 验证文件格式
- 检查必需文件是否存在
- 返回模型文件路径字典
- 支持 GCS 和本地文件系统
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class ManifestError(Exception):
    """Manifest 相关错误"""
    pass


class ManifestLoader:
    """Manifest 加载器"""

    def __init__(self, base_path: str, manifest_filename: str = "manifest.json"):
        """
        初始化 Manifest 加载器

        Args:
            base_path: 模型目录路径（可以是本地路径或 GCS 路径）
            manifest_filename: Manifest 文件名（默认 "manifest.json"）
        """
        self.base_path = Path(base_path)
        self.manifest_filename = manifest_filename
        self.manifest_path = self.base_path / manifest_filename
        self.manifest_data = None

    def load(self) -> Dict[str, str]:
        """
        加载 manifest 并返回模型文件路径字典

        Returns:
            模型文件路径字典，格式：{"model_name": "/path/to/model.pkl"}

        Raises:
            ManifestError: 如果 manifest 文件不存在或格式错误
            FileNotFoundError: 如果必需的模型文件不存在
        """
        # 1. 读取 manifest 文件
        self.manifest_data = self._read_manifest()

        # 2. 验证格式
        self._validate_manifest()

        # 3. 构建模型路径字典
        model_paths = self._build_model_paths()

        # 4. 验证文件存在性
        self._verify_files(model_paths)

        logger.info(f"Successfully loaded manifest from {self.manifest_path}")
        logger.info(f"Found {len(model_paths)} model files")

        return model_paths

    def _read_manifest(self) -> Dict:
        """读取 manifest.json 文件"""
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

    def _validate_manifest(self):
        """验证 manifest 格式"""
        if not isinstance(self.manifest_data, dict):
            raise ManifestError("Manifest must be a JSON object")

        # 检查必需字段
        if 'models' not in self.manifest_data:
            raise ManifestError("Manifest must contain 'models' field")

        if not isinstance(self.manifest_data['models'], dict):
            raise ManifestError("'models' field must be a dictionary")

        if len(self.manifest_data['models']) == 0:
            raise ManifestError("'models' field cannot be empty")

        # 验证每个模型条目
        for model_name, model_info in self.manifest_data['models'].items():
            if not isinstance(model_info, dict):
                raise ManifestError(f"Model '{model_name}' info must be a dictionary")

            if 'file' not in model_info:
                raise ManifestError(f"Model '{model_name}' must have 'file' field")

    def _build_model_paths(self) -> Dict[str, str]:
        """构建模型文件路径字典"""
        model_paths = {}

        for model_name, model_info in self.manifest_data['models'].items():
            # 获取文件名
            filename = model_info['file']

            # 构建完整路径
            full_path = self.base_path / filename

            model_paths[model_name] = str(full_path)

        return model_paths

    def _verify_files(self, model_paths: Dict[str, str]):
        """验证模型文件是否存在"""
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
                    # 从字典中移除不存在的可选文件
                    del model_paths[model_name]

    def get_metadata(self) -> Dict[str, Any]:
        """
        获取 manifest 中的元数据

        Returns:
            元数据字典
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
        获取特定模型的信息

        Args:
            model_name: 模型名称

        Returns:
            模型信息字典，如果不存在则返回 None
        """
        if self.manifest_data is None:
            raise ManifestError("Manifest not loaded yet. Call load() first.")

        return self.manifest_data['models'].get(model_name)

    def list_models(self) -> list:
        """
        列出所有模型名称

        Returns:
            模型名称列表
        """
        if self.manifest_data is None:
            raise ManifestError("Manifest not loaded yet. Call load() first.")

        return list(self.manifest_data['models'].keys())


def load_models_from_manifest(base_path: str, manifest_filename: str = "manifest.json") -> Dict[str, str]:
    """
    便捷函数：从 manifest 加载模型路径

    Args:
        base_path: 模型目录路径
        manifest_filename: Manifest 文件名

    Returns:
        模型文件路径字典

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
