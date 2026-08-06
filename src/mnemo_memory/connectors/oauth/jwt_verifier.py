"""Pinned-key JWT verifier for an OAuth-protected MCP resource server."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

import jwt
from mcp.server.auth.provider import AccessToken

from mnemo_memory.packages.domain import OwnerId

_ALGORITHMS = frozenset({"ES256", "PS256", "RS256"})


@dataclass(frozen=True, slots=True)
class JwtVerifierConfig:
    issuer: str
    audience: str
    public_key_pem: str
    required_scopes: tuple[str, ...] = ("mnemo:context",)
    algorithm: str = "RS256"
    leeway_seconds: int = 30

    def __post_init__(self) -> None:
        _require_https_url(self.issuer, "OAuth issuer")
        _require_https_url(self.audience, "OAuth resource audience")
        if not isinstance(self.public_key_pem, str) or not self.public_key_pem.startswith(
            "-----BEGIN PUBLIC KEY-----"
        ):
            raise ValueError("OAuth public key must be a PEM public key")
        scopes = tuple(self.required_scopes)
        if (
            not 1 <= len(scopes) <= 16
            or len(set(scopes)) != len(scopes)
            or any(
                not isinstance(scope, str)
                or not scope
                or len(scope) > 128
                or any(character.isspace() for character in scope)
                for scope in scopes
            )
        ):
            raise ValueError("OAuth required scopes are invalid")
        if self.algorithm not in _ALGORITHMS:
            raise ValueError("OAuth JWT algorithm is not approved")
        if (
            not isinstance(self.leeway_seconds, int)
            or isinstance(self.leeway_seconds, bool)
            or not 0 <= self.leeway_seconds <= 60
        ):
            raise ValueError("OAuth clock leeway must be between 0 and 60 seconds")
        object.__setattr__(self, "required_scopes", scopes)


class MnemoJwtTokenVerifier:
    """Validate one signed bearer token without retaining or logging its contents."""

    def __init__(self, config: JwtVerifierConfig) -> None:
        self._config = config

    async def verify_token(self, token: str) -> AccessToken | None:
        if not isinstance(token, str) or not 1 <= len(token) <= 8_192:
            return None
        try:
            claims = jwt.decode(
                token,
                self._config.public_key_pem,
                algorithms=[self._config.algorithm],
                audience=self._config.audience,
                issuer=self._config.issuer,
                leeway=self._config.leeway_seconds,
                options={"require": ["aud", "exp", "iat", "iss", "sub"]},
            )
            subject = claims["sub"]
            client_id = claims.get("client_id", claims.get("azp"))
            expires_at = claims["exp"]
            scopes = _scopes(claims.get("scope"))
            if not isinstance(subject, str) or str(OwnerId.from_string(subject)) != subject:
                return None
            if (
                not isinstance(client_id, str)
                or not 1 <= len(client_id) <= 256
                or any(character.isspace() for character in client_id)
            ):
                return None
            if not isinstance(expires_at, int) or isinstance(expires_at, bool):
                return None
            if not set(self._config.required_scopes).issubset(scopes):
                return None
        except (KeyError, TypeError, ValueError, jwt.PyJWTError):
            return None
        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=sorted(scopes),
            expires_at=expires_at,
            resource=self._config.audience,
            subject=subject,
            claims={"iss": self._config.issuer},
        )


def _scopes(value: object) -> frozenset[str]:
    if isinstance(value, str):
        scopes = value.split()
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        scopes = value
    else:
        raise ValueError("OAuth token scope is invalid")
    if (
        not 1 <= len(scopes) <= 64
        or len(set(scopes)) != len(scopes)
        or any(not scope or len(scope) > 128 for scope in scopes)
    ):
        raise ValueError("OAuth token scope is invalid")
    return frozenset(scopes)


def _require_https_url(value: str, label: str) -> None:
    if not isinstance(value, str) or len(value) > 2_048:
        raise ValueError(f"{label} is invalid")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.query
    ):
        raise ValueError(f"{label} must be an HTTPS URL without credentials, query, or fragment")
