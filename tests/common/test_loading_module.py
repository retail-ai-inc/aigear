import pytest
from aigear.common.loading_module import LoadModule


def test_load_module_returns_none_when_path_is_none():
    loader = LoadModule(model_path=None)
    assert loader.load_module() is None


def test_load_module_returns_none_when_path_has_no_dot():
    loader = LoadModule(model_path="nomodulepath")
    assert loader.load_module() is None


def test_load_module_loads_class_from_valid_path():
    loader = LoadModule(model_path="pathlib.Path")
    result = loader.load_module()
    from pathlib import Path
    assert result is Path


def test_load_module_loads_builtin_class():
    loader = LoadModule(model_path="json.JSONDecoder")
    result = loader.load_module()
    import json
    assert result is json.JSONDecoder


def test_load_module_loads_function():
    loader = LoadModule(model_path="os.path.join")
    result = loader.load_module()
    import os
    assert result is os.path.join


def test_load_module_raises_on_nonexistent_module():
    loader = LoadModule(model_path="nonexistent_pkg.module.MyClass")
    with pytest.raises(ModuleNotFoundError):
        loader.load_module()


def test_load_module_raises_on_nonexistent_attribute():
    loader = LoadModule(model_path="json.NonExistentClass")
    with pytest.raises(AttributeError):
        loader.load_module()


def test_load_module_handles_deeply_nested_path():
    loader = LoadModule(model_path="os.path.sep")
    result = loader.load_module()
    import os
    assert result == os.path.sep
