from __future__ import annotations

import warnings
from pathlib import Path
from unittest.mock import patch

import pytest

from aigear.cli.doctor import DoctorFinding, doctor_cli, format_report, scan_project


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ── scan_project ──────────────────────────────────────────────────────────────


def test_scan_project_returns_empty_for_clean_project(tmp_path):
    _write(tmp_path / "app.py", "print('hello world')\n")
    assert scan_project(tmp_path) == []


def test_scan_project_detects_legacy_asset_management_import(tmp_path):
    _write(
        tmp_path / "app.py",
        "from aigear.management.asset import AssetManagement\n"
        "manager = AssetManagement()\n",
    )
    findings = scan_project(tmp_path)
    kinds = {f.kind for f in findings}
    assert "legacy_asset_management" in kinds


def test_scan_project_detects_versioned_asset_management_usage(tmp_path):
    _write(
        tmp_path / "app.py",
        "from aigear.management.versioned_asset import VersionedAssetManagement\n",
    )
    findings = scan_project(tmp_path)
    kinds = {f.kind for f in findings}
    assert "versioned_asset_management" in kinds
    # "VersionedAssetManagement" must not also be double-counted as bare "AssetManagement".
    assert "legacy_asset_management" not in kinds


def test_scan_project_detects_register_external_call(tmp_path):
    _write(tmp_path / "app.py", "manager.register_external(ref)\n")
    findings = scan_project(tmp_path)
    assert any(f.kind == "register_external_call" for f in findings)


def test_scan_project_detects_get_run_asset_path_call(tmp_path):
    _write(tmp_path / "app.py", "path = manager.get_run_asset_path(run_id)\n")
    findings = scan_project(tmp_path)
    assert any(f.kind == "get_run_asset_path_call" for f in findings)


def test_scan_project_reports_correct_file_and_line_number(tmp_path):
    _write(tmp_path / "app.py", "\n\nmanager = AssetManagement()\n")
    findings = scan_project(tmp_path)
    assert len(findings) == 1
    assert findings[0].file == tmp_path / "app.py"
    assert findings[0].line_number == 3


def test_scan_project_skips_excluded_directories(tmp_path):
    _write(tmp_path / ".venv" / "lib" / "site.py", "AssetManagement()\n")
    _write(tmp_path / "node_modules" / "pkg" / "index.py", "AssetManagement()\n")
    assert scan_project(tmp_path) == []


def test_scan_project_skips_unreadable_files_without_raising(tmp_path):
    good = tmp_path / "app.py"
    _write(good, "AssetManagement()\n")

    real_read_text = Path.read_text

    def _flaky_read_text(self, *args, **kwargs):
        if self.name == "app.py":
            raise OSError("simulated permission error")
        return real_read_text(self, *args, **kwargs)

    with patch.object(Path, "read_text", _flaky_read_text):
        findings = scan_project(tmp_path)
    assert findings == []


def test_scan_project_recurses_into_nested_directories(tmp_path):
    _write(tmp_path / "pkg" / "sub" / "mod.py", "AssetManagement()\n")
    findings = scan_project(tmp_path)
    assert len(findings) == 1


# ── format_report ─────────────────────────────────────────────────────────────


def test_format_report_clean_project_mentions_no_usage(tmp_path):
    report = format_report(tmp_path, [])
    assert "No legacy Pipeline asset management usage detected" in report
    assert "no warnings" in report


def test_format_report_includes_migration_guide_pointer(tmp_path):
    finding = DoctorFinding(
        kind="legacy_asset_management",
        file=tmp_path / "app.py",
        line_number=1,
        line_text="AssetManagement()",
    )
    report = format_report(tmp_path, [finding])
    assert "pipeline-v2-migration-guide.md" in report
    assert "app.py:1" in report


# ── doctor_cli (entry point) ───────────────────────────────────────────────────


def test_doctor_cli_prints_report_for_clean_project(tmp_path, capsys):
    _write(tmp_path / "app.py", "print('hello')\n")
    with patch("sys.argv", ["aigear-doctor", "--path", str(tmp_path)]):
        doctor_cli()
    captured = capsys.readouterr()
    assert "No legacy Pipeline asset management usage detected" in captured.out


def test_doctor_cli_runs_without_env_json_present(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert not (tmp_path / "env.json").exists()
    with patch("sys.argv", ["aigear-doctor"]):
        doctor_cli()  # must not raise
    captured = capsys.readouterr()
    assert "aigear doctor" in captured.out


def test_doctor_cli_emits_no_python_warnings(tmp_path):
    _write(tmp_path / "app.py", "AssetManagement()\nmanager.register_external(x)\n")
    with patch("sys.argv", ["aigear-doctor", "--path", str(tmp_path)]):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            doctor_cli()
    assert caught == []
