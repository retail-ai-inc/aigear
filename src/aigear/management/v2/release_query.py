"""Signed, bounded keyset queries for release history and impact reports."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Tuple

from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.identifiers import TypedId

__all__ = [
    "ReleaseQueryError",
    "SignedReleasePageToken",
    "ReleaseQueryPage",
    "list_release_history",
    "list_release_impact",
]


class ReleaseQueryError(ValueError):
    pass


_HISTORY_ORDER = "release-history.v2:(created_at,operation_id)"
_IMPACT_ORDER = "release-impact.v2:(created_at,release_id)"


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@dataclass(frozen=True)
class SignedReleasePageToken:
    query_kind: str
    filter_digest: str
    order: str
    database_id: str
    cutoff: str
    cursor: Tuple[str, str]

    def payload(self) -> list:
        return [
            "2.0",
            self.query_kind,
            self.filter_digest,
            self.order,
            self.database_id,
            self.cutoff,
            list(self.cursor),
        ]

    def encode(self, signing_key: bytes) -> str:
        _signing_key(signing_key)
        raw = json.dumps(self.payload(), separators=(",", ":")).encode("utf-8")
        signature = hmac.new(signing_key, raw, hashlib.sha256).digest()
        return f"{_b64encode(raw)}.{_b64encode(signature)}"

    @classmethod
    def decode(cls, value: str, signing_key: bytes) -> "SignedReleasePageToken":
        _signing_key(signing_key)
        try:
            payload_text, signature_text = value.split(".", 1)
            raw = _b64decode(payload_text)
            signature = _b64decode(signature_text)
            expected = hmac.new(signing_key, raw, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise ReleaseQueryError("page token signature is invalid")
            schema, kind, digest, order, database, cutoff, cursor = json.loads(raw)
            if schema != "2.0":
                raise ReleaseQueryError("page token schema is unsupported")
            token = cls(kind, digest, order, database, cutoff, tuple(cursor))
            _parse_time(token.cutoff)
            if len(token.cursor) != 2 or not all(token.cursor):
                raise ReleaseQueryError("page token cursor is invalid")
            return token
        except ReleaseQueryError:
            raise
        except Exception as exc:
            raise ReleaseQueryError("page token is malformed") from exc


@dataclass(frozen=True)
class ReleaseQueryPage:
    items: tuple[object, ...]
    next_page_token: Optional[str]
    cutoff: str


def _signing_key(value: bytes) -> None:
    if not isinstance(value, bytes) or len(value) < 32:
        raise ReleaseQueryError("page token signing key must be at least 32 bytes")


def _parse_time(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ReleaseQueryError("cutoff must be an ISO timestamp") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ReleaseQueryError("cutoff must be timezone-aware")
    return result.astimezone(timezone.utc)


def _request(
    *,
    query_kind: str,
    filter_digest: str,
    order: str,
    database_id: str,
    page_size: int,
    page_token: Optional[str],
    now: datetime,
    signing_key: bytes,
) -> tuple[str, Optional[tuple[str, str]]]:
    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 500:
        raise ReleaseQueryError("page_size must be an int in [1, 500]")
    _signing_key(signing_key)
    if page_token is None:
        return _parse_time(now.isoformat()).isoformat(), None
    token = SignedReleasePageToken.decode(page_token, signing_key)
    expected = (query_kind, filter_digest, order, database_id)
    actual = (token.query_kind, token.filter_digest, token.order, token.database_id)
    if actual != expected:
        raise ReleaseQueryError("page token does not match the current query")
    return token.cutoff, token.cursor


def _page(
    records,
    *,
    query_kind: str,
    filter_digest: str,
    order: str,
    database_id: str,
    cutoff: str,
    page_size: int,
    timestamp_field: str,
    identity,
    signing_key: bytes,
) -> ReleaseQueryPage:
    records = tuple(records)
    items = records[:page_size]
    next_token = None
    if len(records) > page_size:
        last = items[-1]
        next_token = SignedReleasePageToken(
            query_kind=query_kind,
            filter_digest=filter_digest,
            order=order,
            database_id=database_id,
            cutoff=cutoff,
            cursor=(getattr(last, timestamp_field), identity(last)),
        ).encode(signing_key)
    return ReleaseQueryPage(items, next_token, cutoff)


def list_release_history(
    registry,
    *,
    service_name: str,
    signing_key: bytes,
    now: datetime,
    database_id: str = "(default)",
    page_size: int = 100,
    page_token: Optional[str] = None,
) -> ReleaseQueryPage:
    if not isinstance(service_name, str) or not service_name:
        raise ReleaseQueryError("service_name must be non-empty")
    digest = digest_sha256_of_jcs(["release-history.v2", service_name])
    cutoff, cursor = _request(
        query_kind="history",
        filter_digest=digest,
        order=_HISTORY_ORDER,
        database_id=database_id,
        page_size=page_size,
        page_token=page_token,
        now=now,
        signing_key=signing_key,
    )
    records = registry.query_release_operations(
        service_name=service_name,
        cutoff=cutoff,
        cursor=cursor,
        limit=page_size + 1,
    )
    return _page(
        records,
        query_kind="history",
        filter_digest=digest,
        order=_HISTORY_ORDER,
        database_id=database_id,
        cutoff=cutoff,
        page_size=page_size,
        timestamp_field="created_at",
        identity=lambda record: record.operation_id,
        signing_key=signing_key,
    )


def list_release_impact(
    registry,
    *,
    asset_version_id: TypedId,
    signing_key: bytes,
    now: datetime,
    database_id: str = "(default)",
    page_size: int = 100,
    page_token: Optional[str] = None,
) -> ReleaseQueryPage:
    if not isinstance(asset_version_id, TypedId):
        raise ReleaseQueryError("asset_version_id must be a TypedId")
    digest = digest_sha256_of_jcs(["release-impact.v2", asset_version_id.typed])
    cutoff, cursor = _request(
        query_kind="impact",
        filter_digest=digest,
        order=_IMPACT_ORDER,
        database_id=database_id,
        page_size=page_size,
        page_token=page_token,
        now=now,
        signing_key=signing_key,
    )
    records = registry.query_releases_by_asset(
        asset_version_id=asset_version_id,
        cutoff=cutoff,
        cursor=cursor,
        limit=page_size + 1,
    )
    return _page(
        records,
        query_kind="impact",
        filter_digest=digest,
        order=_IMPACT_ORDER,
        database_id=database_id,
        cutoff=cutoff,
        page_size=page_size,
        timestamp_field="created_at",
        identity=lambda record: record.release_id.typed,
        signing_key=signing_key,
    )
