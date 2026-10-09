from types import SimpleNamespace

import pytest

from tests.conftest import pytest_configure


def _config(root, cleanup, basetemp=None):
    return SimpleNamespace(
        rootpath=root,
        option=SimpleNamespace(basetemp=basetemp),
        add_cleanup=cleanup.append,
    )


def test_sessions_use_distinct_directories_and_cleanup_only_their_own_files(tmp_path):
    cleanup = []
    first = _config(tmp_path, cleanup)
    second = _config(tmp_path, cleanup)
    sentinel = tmp_path / "preserve.txt"
    sentinel.write_text("keep", encoding="utf-8")
    try:
        pytest_configure(first)
        pytest_configure(second)
        assert first.option.basetemp != second.option.basetemp
        from pathlib import Path

        first_path = Path(first.option.basetemp)
        second_path = Path(second.option.basetemp)
        assert first_path.parent.parent == tmp_path / ".pytest_cache/tmp"
        first_path.mkdir()
        (first_path / "fixture.txt").write_text("temporary", encoding="utf-8")
        cleanup[0]()
        assert not first_path.parent.exists()
        assert second_path.parent.is_dir()
        assert sentinel.read_text(encoding="utf-8") == "keep"
    finally:
        for callback in cleanup:
            callback()


def test_explicit_basetemp_is_preserved_without_registering_cleanup(tmp_path):
    cleanup = []
    config = _config(tmp_path, cleanup, basetemp=str(tmp_path / "custom"))
    pytest_configure(config)
    assert config.option.basetemp == str(tmp_path / "custom")
    assert cleanup == []
    assert not (tmp_path / ".pytest_cache").exists()


def test_unusable_cache_path_produces_actionable_configuration_error(tmp_path):
    (tmp_path / ".pytest_cache").write_text("not a directory", encoding="utf-8")
    config = _config(tmp_path, [])
    with pytest.raises(pytest.UsageError, match="Use --basetemp with a writable directory"):
        pytest_configure(config)
