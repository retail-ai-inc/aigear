"""Verify Pub/Sub push OIDC identity before accepting worker completion."""

from __future__ import annotations

from typing import Iterable, Optional

__all__ = ["PubSubAuthenticationError", "verify_pubsub_oidc_token"]


class PubSubAuthenticationError(ValueError):
    pass


def verify_pubsub_oidc_token(
    token: str,
    *,
    audience: str,
    allowed_service_accounts: Iterable[str],
    request: Optional[object] = None,
) -> str:
    if not token or not audience:
        raise PubSubAuthenticationError("token and audience are required")
    allowed = frozenset(allowed_service_accounts)
    if not allowed:
        raise PubSubAuthenticationError("allowed_service_accounts must be non-empty")
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import id_token
    except ImportError as exc:  # pragma: no cover - optional GCP dependency
        raise PubSubAuthenticationError("Pub/Sub OIDC verification requires google-auth") from exc
    try:
        claims = id_token.verify_oauth2_token(token, request or Request(), audience=audience)
    except BaseException as exc:
        raise PubSubAuthenticationError("invalid Pub/Sub OIDC token") from exc
    if claims.get("iss") not in ("https://accounts.google.com", "accounts.google.com"):
        raise PubSubAuthenticationError("unexpected OIDC issuer")
    if claims.get("email_verified") is not True:
        raise PubSubAuthenticationError("OIDC email is not verified")
    principal = claims.get("email")
    if principal not in allowed:
        raise PubSubAuthenticationError("OIDC service account is not allowlisted")
    return principal
