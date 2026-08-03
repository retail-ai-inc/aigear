from __future__ import annotations

import json

import pytest

from aigear.deploy.common.build_context import (
    BuildContextViolation,
    scan_build_context,
)


def _context(tmp_path, dockerfile="COPY src/ ./src/\n", ignore="env.json\ntests/\n"):
    (tmp_path / "Dockerfile.pl").write_text(dockerfile, encoding="utf-8")
    (tmp_path / "Dockerfile.pl.dockerignore").write_text(ignore, encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('safe')\n", encoding="utf-8")
    return tmp_path / "Dockerfile.pl"


def _scan(tmp_path, **kwargs):
    return scan_build_context(
        tmp_path,
        tmp_path / "Dockerfile.pl",
        allowed_copy_roots=("src", "requirements_pl.txt"),
        **kwargs,
    )


def test_safe_context_returns_content_addressed_manifest(tmp_path):
    _context(tmp_path)

    first = _scan(tmp_path)
    second = _scan(tmp_path)

    assert first == second
    assert first.schema_version == "1.0"
    assert any(item.path == "src/app.py" for item in first.files)
    assert len(first.manifest_sha256) == 64
    assert "print('safe')" not in json.dumps(first.to_dict())


def test_ignored_environment_and_test_files_do_not_enter_manifest(tmp_path):
    _context(tmp_path)
    (tmp_path / "env.json").write_text('{"token":"not-a-real-token"}', encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "fixture.json").write_text("sample", encoding="utf-8")

    manifest = _scan(tmp_path)

    paths = {item.path for item in manifest.files}
    assert "env.json" not in paths
    assert "tests/fixture.json" not in paths


@pytest.mark.parametrize(
    "relative,content,category",
    [
        ("env.json", "{}", "environment_config"),
        ("deploy.key", "placeholder", "credential_file"),
        ("tests/fixture.json", "sample", "test_data"),
        ("src/settings.py", "API_TOKEN=abcdefghijklmnop", "secret_content"),
    ],
)
def test_included_sensitive_material_is_rejected_without_path_or_value(
    tmp_path, relative, content, category
):
    _context(tmp_path, ignore="")
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

    with pytest.raises(BuildContextViolation) as excinfo:
        _scan(tmp_path)

    message = str(excinfo.value)
    assert category in excinfo.value.categories
    assert relative not in message
    assert content not in message
    assert "rotate exposed credentials" in message
    assert "rebuild affected images" in message


@pytest.mark.parametrize(
    "instruction",
    [
        "COPY . .",
        "COPY * /app/",
        "COPY ../outside /app/",
        "COPY config/ /app/config/",
        "ADD src.tar /app/",
    ],
)
def test_broad_or_non_allowlisted_copy_is_rejected(tmp_path, instruction):
    _context(tmp_path, dockerfile=f"{instruction}\n")

    with pytest.raises(BuildContextViolation) as excinfo:
        _scan(tmp_path)

    assert set(excinfo.value.categories).intersection(
        {"docker_copy", "docker_instruction"}
    )


def test_multistage_copy_does_not_treat_stage_path_as_context_source(tmp_path):
    _context(
        tmp_path,
        dockerfile="COPY src/ ./src/\nCOPY --from=builder /venv /venv\n",
    )

    manifest = _scan(tmp_path)

    assert manifest.files
