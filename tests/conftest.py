import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from confluo_api.main import create_app
from confluo_core.auth import TokenVerifier
from confluo_core.settings import Settings

KID = "test-key"


class StaticJWKS(jwt.PyJWKClient):
    """A JWKS client serving one in-memory public key instead of fetching over HTTP."""

    def __init__(self, public_key: ec.EllipticCurvePublicKey) -> None:
        super().__init__("http://unused.invalid/jwks.json")
        jwk = jwt.algorithms.ECAlgorithm.to_jwk(public_key, as_dict=True)
        self._key_set = jwt.PyJWKSet.from_dict({"keys": [{**jwk, "kid": KID, "alg": "ES256"}]})

    def get_jwk_set(self, refresh: bool = False) -> jwt.PyJWKSet:
        return self._key_set


@pytest.fixture(scope="session")
def signing_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture
def settings() -> Settings:
    # Port 1 is never listening, so the database check fails fast without a real DB.
    return Settings(database_url="postgresql://x:x@127.0.0.1:1/x")


MakeToken = Callable[..., str]


@pytest.fixture
def make_token(signing_key: ec.EllipticCurvePrivateKey, settings: Settings) -> MakeToken:
    def make(
        key: ec.EllipticCurvePrivateKey | None = None, kid: str = KID, **overrides: Any
    ) -> str:
        now = int(time.time())
        claims = {
            "sub": str(uuid.uuid4()),
            "email": "staff@example.com",
            "aud": settings.supabase_jwt_audience,
            "iss": settings.jwt_issuer,
            "role": "authenticated",
            "iat": now,
            "exp": now + 3600,
            **overrides,
        }
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, key or signing_key, algorithm="ES256", headers={"kid": kid})

    return make


@pytest.fixture
def verifier(signing_key: ec.EllipticCurvePrivateKey, settings: Settings) -> TokenVerifier:
    return TokenVerifier(settings, jwks_client=StaticJWKS(signing_key.public_key()))


@pytest.fixture
def client(verifier: TokenVerifier, settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, token_verifier=verifier)) as c:
        yield c
