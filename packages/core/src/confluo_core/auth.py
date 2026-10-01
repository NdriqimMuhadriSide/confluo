"""Staff authentication: verify Supabase Auth access tokens (ARCHITECTURE.md §5, decision 1).

Supabase signs access tokens with an asymmetric key (ES256) and publishes the public
keys at `<supabase_url>/auth/v1/.well-known/jwks.json`. We verify signature, expiry,
issuer and audience locally, so a request costs no round trip to Supabase once the
keys are cached.
"""

from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from confluo_core.settings import Settings

ALGORITHMS = ["ES256", "RS256"]


@dataclass(frozen=True)
class AuthUser:
    id: UUID
    email: str | None
    claims: dict[str, Any]


class TokenVerifier:
    def __init__(self, settings: Settings, jwks_client: jwt.PyJWKClient | None = None) -> None:
        self.issuer = settings.jwt_issuer
        self.audience = settings.supabase_jwt_audience
        # PyJWKClient caches the key set and refetches when it sees an unknown `kid`,
        # so key rotation in Supabase needs no restart.
        self._jwks = jwks_client or jwt.PyJWKClient(
            f"{settings.supabase_auth_url}/.well-known/jwks.json",
            cache_keys=True,
            lifespan=600,
            timeout=5,
        )

    def _verify_sync(self, token: str) -> AuthUser:
        signing_key = self._jwks.get_signing_key_from_jwt(token)
        claims: dict[str, Any] = jwt.decode(
            token,
            signing_key.key,
            algorithms=ALGORITHMS,
            audience=self.audience,
            issuer=self.issuer,
            options={"require": ["exp", "sub", "aud", "iss"]},
        )
        return AuthUser(id=UUID(claims["sub"]), email=claims.get("email"), claims=claims)

    async def verify(self, token: str) -> AuthUser:
        """Return the user for a valid token; raise `jwt.PyJWTError` otherwise."""
        return await run_in_threadpool(self._verify_sync, token)


_bearer = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AuthUser:
    """FastAPI dependency: the signed-in staff user, or 401."""
    if credentials is None:
        raise _unauthorized("Missing bearer token")
    verifier: TokenVerifier = request.app.state.token_verifier
    try:
        return await verifier.verify(credentials.credentials)
    except jwt.ExpiredSignatureError:
        raise _unauthorized("Token expired") from None
    except (jwt.PyJWTError, ValueError):
        raise _unauthorized("Invalid token") from None


CurrentUser = Annotated[AuthUser, Depends(current_user)]
