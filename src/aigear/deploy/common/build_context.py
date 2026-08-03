"""Fail-closed Docker build-context and Dockerfile scanner."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence

__all__ = [
    "BuildContextManifest",
    "BuildContextViolation",
    "BuildFile",
    "scan_build_context",
]


_GLOB_CHARS = "*?["
_SENSITIVE_SUFFIXES = {".key", ".pem", ".p12", ".pfx", ".jks", ".keystore"}
_SENSITIVE_NAMES = {
    ".env",
    "env.json",
    "credentials.json",
    "service-account.json",
    "service_account.json",
}
_SECRET_CONTENT = tuple(
    re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    for pattern in (
        rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
        rb'"private_key"\s*:',
        rb"^(?:[A-Z0-9_]*(?:API_?KEY|TOKEN|SECRET|PASSWORD))\s*[:=]\s*\S{8,}",
        rb"^(?:ENV|ARG)\s+[A-Z0-9_]*(?:API_?KEY|TOKEN|SECRET|PASSWORD)\s*=?\s*\S{8,}",
        rb"\bgh[pousr]_[A-Za-z0-9_]{20,}\b",
        rb"\bAKIA[0-9A-Z]{16}\b",
    )
)


class BuildContextViolation(ValueError):
    def __init__(self, categories: Iterable[str]):
        self.categories = tuple(sorted(set(categories)))
        joined = ",".join(self.categories)
        super().__init__(
            "build security guard rejected path categories="
            f"{joined}; rotate exposed credentials and rebuild affected images"
        )


@dataclass(frozen=True)
class BuildFile:
    path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class BuildContextManifest:
    schema_version: str
    dockerfile_sha256: str
    files: tuple[BuildFile, ...]
    manifest_sha256: str

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "dockerfile_sha256": self.dockerfile_sha256,
            "files": [
                {
                    "path": item.path,
                    "size_bytes": item.size_bytes,
                    "sha256": item.sha256,
                }
                for item in self.files
            ],
            "manifest_sha256": self.manifest_sha256,
        }


@dataclass(frozen=True)
class _IgnoreRule:
    pattern: str
    negated: bool
    directory_only: bool

    def matches(self, path: str, *, is_dir: bool) -> bool:
        pattern = self.pattern
        if self.directory_only:
            if _match_path(path, pattern):
                return True
            return any(_match_path(parent, pattern) for parent in _parents(path))
        return _match_path(path, pattern)


def _parents(path: str) -> tuple[str, ...]:
    parts = path.split("/")
    return tuple("/".join(parts[:index]) for index in range(1, len(parts)))


def _match_path(path: str, pattern: str) -> bool:
    if "/" not in pattern:
        return any(fnmatch.fnmatchcase(part, pattern) for part in path.split("/"))
    return fnmatch.fnmatchcase(path, pattern) or PurePosixPath(path).match(pattern)


def _ignore_rules(path: Path) -> tuple[_IgnoreRule, ...]:
    rules = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        value = raw.strip()
        if not value or value.startswith("#"):
            continue
        negated = value.startswith("!")
        if negated:
            value = value[1:]
        value = value.replace("\\", "/").lstrip("/")
        directory_only = value.endswith("/")
        value = value.rstrip("/")
        if not value or value == ".":
            continue
        rules.append(_IgnoreRule(value, negated, directory_only))
    return tuple(rules)


def _is_ignored(path: str, *, is_dir: bool, rules: Sequence[_IgnoreRule]) -> bool:
    ignored = False
    for rule in rules:
        if rule.matches(path, is_dir=is_dir):
            ignored = not rule.negated
    return ignored


def _logical_dockerfile_lines(content: str) -> tuple[str, ...]:
    lines = []
    current = ""
    for raw in content.splitlines():
        stripped = raw.strip()
        if not current and (not stripped or stripped.startswith("#")):
            continue
        current = f"{current} {stripped}".strip()
        if current.endswith("\\"):
            current = current[:-1].rstrip()
            continue
        lines.append(current)
        current = ""
    if current:
        lines.append(current)
    return tuple(lines)


def _copy_sources(line: str) -> tuple[str, ...] | None:
    instruction, _, arguments = line.partition(" ")
    instruction = instruction.upper()
    if instruction == "ADD":
        raise BuildContextViolation(("docker_instruction",))
    if instruction != "COPY":
        return None
    arguments = arguments.strip()
    if arguments.startswith("["):
        try:
            values = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise BuildContextViolation(("docker_instruction",)) from exc
        if not isinstance(values, list) or len(values) < 2:
            raise BuildContextViolation(("docker_instruction",))
        return tuple(values[:-1])
    try:
        values = shlex.split(arguments, posix=True)
    except ValueError as exc:
        raise BuildContextViolation(("docker_instruction",)) from exc
    while values and values[0].startswith("--"):
        if values[0].startswith("--from="):
            return ()
        values.pop(0)
    if len(values) < 2:
        raise BuildContextViolation(("docker_instruction",))
    return tuple(values[:-1])


def _validate_copy_allowlist(content: str, allowed_roots: Sequence[str]) -> None:
    allowed = tuple(root.replace("\\", "/").strip("/") for root in allowed_roots)
    categories = []
    for line in _logical_dockerfile_lines(content):
        sources = _copy_sources(line)
        if sources is None:
            continue
        for source in sources:
            normalized = source.replace("\\", "/").rstrip("/")
            if normalized.startswith("./"):
                normalized = normalized[2:]
            broad = (
                not normalized
                or normalized == "."
                or normalized.startswith("/")
                or normalized == ".."
                or normalized.startswith("../")
                or any(char in normalized for char in _GLOB_CHARS)
            )
            permitted = any(
                normalized == root or normalized.startswith(f"{root}/")
                for root in allowed
            )
            if broad or not permitted:
                categories.append("docker_copy")
    if categories:
        raise BuildContextViolation(categories)


def _path_category(path: str) -> str | None:
    pure = PurePosixPath(path)
    lower_name = pure.name.lower()
    lower_parts = {part.lower() for part in pure.parts}
    if lower_name in _SENSITIVE_NAMES:
        return "environment_config" if lower_name in {".env", "env.json"} else "credential_file"
    if pure.suffix.lower() in _SENSITIVE_SUFFIXES or any(
        marker in lower_name for marker in ("private_key", "credential", "access_token")
    ):
        return "credential_file"
    if lower_parts.intersection({"tests", "testdata", "fixtures"}):
        return "test_data"
    return None


def _content_has_secret(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            sample = stream.read(2 * 1024 * 1024)
    except OSError:
        return True
    return any(pattern.search(sample) for pattern in _SECRET_CONTENT)


def _context_files(context: Path, rules: Sequence[_IgnoreRule]):
    for root, directories, files in os.walk(context, followlinks=False):
        root_path = Path(root)
        for name in tuple(directories):
            candidate = root_path / name
            relative = candidate.relative_to(context).as_posix()
            if candidate.is_symlink() and not _is_ignored(
                relative, is_dir=True, rules=rules
            ):
                yield candidate, relative, "linked_path"
        for name in files:
            candidate = root_path / name
            relative = candidate.relative_to(context).as_posix()
            if _is_ignored(relative, is_dir=False, rules=rules):
                continue
            category = "linked_path" if candidate.is_symlink() else _path_category(relative)
            yield candidate, relative, category


def scan_build_context(
    context_path: str | Path,
    dockerfile_path: str | Path,
    *,
    allowed_copy_roots: Sequence[str],
    dockerignore_path: str | Path | None = None,
) -> BuildContextManifest:
    context = Path(context_path).resolve(strict=True)
    dockerfile = Path(dockerfile_path).resolve(strict=True)
    if not context.is_dir() or not dockerfile.is_file():
        raise BuildContextViolation(("build_binding",))
    try:
        dockerfile.relative_to(context)
    except ValueError as exc:
        raise BuildContextViolation(("build_binding",)) from exc
    if dockerignore_path is None:
        specific = Path(f"{dockerfile}.dockerignore")
        dockerignore = specific if specific.is_file() else context / ".dockerignore"
    else:
        dockerignore = Path(dockerignore_path).resolve(strict=True)
    if not dockerignore.is_file():
        raise BuildContextViolation(("dockerignore",))

    dockerfile_content = dockerfile.read_text(encoding="utf-8")
    if any(
        pattern.search(dockerfile_content.encode("utf-8"))
        for pattern in _SECRET_CONTENT
    ):
        raise BuildContextViolation(("secret_content",))
    _validate_copy_allowlist(dockerfile_content, allowed_copy_roots)
    rules = _ignore_rules(dockerignore)
    violations = []
    files = []
    for path, relative, category in _context_files(context, rules):
        if category is not None:
            violations.append(category)
            continue
        if _content_has_secret(path):
            violations.append("secret_content")
            continue
        content = path.read_bytes()
        files.append(
            BuildFile(relative, len(content), hashlib.sha256(content).hexdigest())
        )
    if violations:
        raise BuildContextViolation(violations)
    files.sort(key=lambda item: item.path)
    dockerfile_sha256 = hashlib.sha256(dockerfile_content.encode("utf-8")).hexdigest()
    payload = {
        "schema_version": "1.0",
        "dockerfile_sha256": dockerfile_sha256,
        "files": [
            {"path": item.path, "size_bytes": item.size_bytes, "sha256": item.sha256}
            for item in files
        ],
    }
    manifest_sha256 = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return BuildContextManifest("1.0", dockerfile_sha256, tuple(files), manifest_sha256)
