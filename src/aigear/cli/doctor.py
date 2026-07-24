"""``aigear doctor`` — non-invasive Pipeline V2 migration advisor.

Per spec section 4.1/4.2 (``docs/pipeline-asset-lifecycle-management-v2.md``),
the only sanctioned way to nudge users toward Pipeline V2 is through
release notes, docs, and *explicitly invoked* diagnostics — never through a
Python ``warnings`` emission during normal ``AssetManagement`` /
``VersionedAssetManagement`` usage. This module implements that diagnostic:
a static source scan that reports where legacy management APIs (and the two
methods spec 4.2 calls out as unsafe under V2 authority,
``register_external`` and ``get_run_asset_path``) are used, with a pointer
to the migration guide.

This command never raises for an ordinary project (no matches, no
``env.json``, unreadable files are skipped) and never emits a Python
``warnings.warn`` — it only prints to stdout, matching the Phase A
acceptance criterion in ``docs/pipeline-v2-phase-a-tasks.md``.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

__all__ = ["DoctorFinding", "scan_project", "format_report", "doctor_cli"]

_EXCLUDED_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "build",
    "dist",
    ".eggs",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
}

_MIGRATION_GUIDE = "docs/pipeline-v2-migration-guide.md"
_SPEC_SECTION_4_2 = "docs/pipeline-asset-lifecycle-management-v2.md (section 4.2)"

# Order matters: reported in this order in the rendered report.
_CHECKS: "List[tuple[str, re.Pattern[str], str]]" = [
    (
        "legacy_asset_management",
        re.compile(r"\baigear\.management\.asset\b|\bAssetManagement\b"),
        "Legacy `AssetManagement` remains fully supported (v0.2.0 behavior is "
        "frozen and pinned by regression tests); no action is required. New "
        f"code should prefer `PipelineAssetManagement` — see {_MIGRATION_GUIDE}.",
    ),
    (
        "versioned_asset_management",
        re.compile(r"\baigear\.management\.versioned_asset\b|\bVersionedAssetManagement\b"),
        "`VersionedAssetManagement` keeps its compatible import/signature/return "
        f"shape. Consider migrating to `PipelineAssetManagement` — see {_MIGRATION_GUIDE}.",
    ),
    (
        "register_external_call",
        re.compile(r"\.register_external\s*\("),
        "`register_external()` behavior differs by authority (V1: metadata-only; "
        f"V2: copy-on-register import). Review {_SPEC_SECTION_4_2} before enabling "
        "V2 authority for this project.",
    ),
    (
        "get_run_asset_path_call",
        re.compile(r"\.get_run_asset_path\s*\("),
        "`get_run_asset_path()` only reflects the V1 `_aigear_runs/...` layout; "
        f"its return value is not a canonical path under V2 authority. Review {_SPEC_SECTION_4_2}.",
    ),
]


@dataclass(frozen=True)
class DoctorFinding:
    kind: str
    file: Path
    line_number: int
    line_text: str


def _is_excluded_dir(name: str) -> bool:
    return name in _EXCLUDED_DIR_NAMES or name.endswith(".egg-info")


def _iter_python_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*.py"):
        if any(_is_excluded_dir(part) for part in path.relative_to(root).parts[:-1]):
            continue
        yield path


def scan_project(root: Path) -> List[DoctorFinding]:
    """Statically scan ``root`` for legacy Pipeline asset management usage.

    Never raises: files that cannot be read (permissions, odd encodings,
    concurrent deletion) are silently skipped rather than aborting the scan.
    """
    findings: List[DoctorFinding] = []
    for path in _iter_python_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for kind, pattern, _advice in _CHECKS:
                if pattern.search(line):
                    findings.append(DoctorFinding(kind, path, line_number, line.strip()))
    return findings


def format_report(root: Path, findings: List[DoctorFinding]) -> str:
    lines = ["aigear doctor — Pipeline V2 migration report", "=" * 46, ""]
    if not findings:
        lines.append(f"No legacy Pipeline asset management usage detected under {root}.")
        lines.append("")
        lines.append("This is only an advisory scan; it made no changes and emitted no warnings.")
        return "\n".join(lines)

    by_kind: "dict[str, List[DoctorFinding]]" = {}
    for finding in findings:
        by_kind.setdefault(finding.kind, []).append(finding)

    lines.append(f"Scanned {root} and found the following, in file:line order:")
    lines.append("")
    for kind, pattern, advice in _CHECKS:
        kind_findings = by_kind.get(kind)
        if not kind_findings:
            continue
        lines.append(f"- {kind} ({len(kind_findings)} occurrence(s)):")
        for finding in kind_findings:
            try:
                display_path = finding.file.relative_to(root)
            except ValueError:
                display_path = finding.file
            lines.append(f"    {display_path}:{finding.line_number}: {finding.line_text}")
        lines.append(f"  Recommendation: {advice}")
        lines.append("")

    lines.append("This is only an advisory scan; it made no changes and emitted no warnings.")
    return "\n".join(lines)


def get_argument() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--path",
        default=".",
        help="Project directory to scan (default: current working directory).",
    )
    return parser.parse_args()


def doctor_cli() -> None:
    args = get_argument()
    root = Path(args.path).resolve()
    findings = scan_project(root)
    print(format_report(root, findings))
