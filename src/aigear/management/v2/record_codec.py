"""Lossless Firestore codec for Pipeline V2 dataclass records."""

from __future__ import annotations

import types
from collections.abc import Mapping as AbcMapping
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any, Dict, Mapping, Tuple, Union, get_args, get_origin, get_type_hints

from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.control_document import parse_schema_version

__all__ = ["RecordCodecError", "encode_record", "decode_record"]


class RecordCodecError(ValueError):
    pass


def encode_record(value: Any) -> Any:
    if isinstance(value, TypedId):
        return value.typed
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: encode_record(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): encode_record(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [encode_record(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool, bytes)):
        return value
    raise RecordCodecError(f"unsupported record value type: {type(value)!r}")


def _decode(annotation: Any, value: Any) -> Any:
    if annotation is Any:
        return value
    if value is None:
        if type(None) in get_args(annotation) or annotation is type(None):
            return None
        raise RecordCodecError(f"None is not valid for {annotation!r}")
    if annotation is TypedId:
        return value if isinstance(value, TypedId) else TypedId.from_typed(value)
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return annotation(value)
    if isinstance(annotation, type) and is_dataclass(annotation):
        if not isinstance(value, Mapping):
            raise RecordCodecError(f"expected mapping for {annotation.__name__}, got {type(value)!r}")
        hints = get_type_hints(annotation)
        known = {field.name for field in fields(annotation)}
        unknown = set(value) - known
        if unknown:
            raise RecordCodecError(
                f"unknown fields for {annotation.__name__}: {sorted(unknown)!r}"
            )
        kwargs = {}
        for field in fields(annotation):
            if field.name in value:
                kwargs[field.name] = _decode(hints.get(field.name, Any), value[field.name])
        return annotation(**kwargs)

    origin = get_origin(annotation)
    args = get_args(annotation)
    union_origins = (Union,)
    if hasattr(types, "UnionType"):
        union_origins += (types.UnionType,)
    if origin in union_origins:
        errors = []
        for candidate in args:
            if candidate is type(None):
                continue
            try:
                return _decode(candidate, value)
            except (RecordCodecError, TypeError, ValueError) as exc:
                errors.append(str(exc))
        raise RecordCodecError(f"value does not match {annotation!r}: {errors!r}")
    if origin in (tuple, Tuple):
        item_type = args[0] if args else Any
        return tuple(_decode(item_type, item) for item in value)
    if origin is list:
        item_type = args[0] if args else Any
        return [_decode(item_type, item) for item in value]
    if origin in (dict, Dict, Mapping, AbcMapping):
        key_type, value_type = args if len(args) == 2 else (Any, Any)
        return {
            _decode(key_type, key): _decode(value_type, item)
            for key, item in value.items()
        }
    if annotation in (str, int, float, bool, bytes):
        if not isinstance(value, annotation):
            raise RecordCodecError(f"expected {annotation.__name__}, got {type(value).__name__}")
        return value
    return value


def decode_record(
    record_type: type,
    value: Mapping[str, Any],
    *,
    expected_environment_fingerprint: TypedId | None = None,
):
    schema_version = value.get("schema_version")
    if schema_version is not None:
        try:
            major, _ = parse_schema_version(schema_version)
        except (TypeError, ValueError) as exc:
            raise RecordCodecError("record schema_version is invalid") from exc
        if major != 2:
            raise RecordCodecError(
                f"unsupported Pipeline V2 record schema major: {major}"
            )
    decoded = _decode(record_type, value)
    if expected_environment_fingerprint is not None:
        actual = getattr(decoded, "environment_fingerprint", None)
        if actual != expected_environment_fingerprint:
            raise RecordCodecError(
                "record environment_fingerprint does not match the active environment"
            )
    return decoded
