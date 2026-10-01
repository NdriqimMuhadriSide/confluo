import time

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from tests.conftest import MakeToken

USER_ID = "6f1c1d1e-8a43-4c55-9f0e-2a3b4c5d6e7f"


def test_valid_token_returns_the_user(client: TestClient, make_token: MakeToken) -> None:
    token = make_token(sub=USER_ID, email="anna@example.com")
    res = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json() == {"id": USER_ID, "email": "anna@example.com"}


def test_missing_token_is_rejected(client: TestClient) -> None:
    res = client.get("/api/me")
    assert res.status_code == 401
    assert res.json() == {"detail": "Missing bearer token"}
    assert res.headers["www-authenticate"] == "Bearer"


def test_expired_token_is_rejected(client: TestClient, make_token: MakeToken) -> None:
    token = make_token(exp=int(time.time()) - 60)
    res = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401
    assert res.json() == {"detail": "Token expired"}


def _bad_tokens(make_token: MakeToken) -> dict[str, str]:
    other_key = ec.generate_private_key(ec.SECP256R1())
    head, _, sig = make_token().split(".")
    return {
        "wrong signing key": make_token(key=other_key),
        "unknown kid": make_token(kid="someone-else"),
        "wrong audience": make_token(aud="anon"),
        "wrong issuer": make_token(iss="https://evil.example.com/auth/v1"),
        "missing sub": make_token(sub=None),
        "sub is not a uuid": make_token(sub="not-a-uuid"),
        "tampered payload": f"{head}.eyJzdWIiOiJ4In0.{sig}",
        "garbage": "not.a.jwt",
    }


@pytest.mark.parametrize(
    "case",
    [
        "wrong signing key",
        "unknown kid",
        "wrong audience",
        "wrong issuer",
        "missing sub",
        "sub is not a uuid",
        "tampered payload",
        "garbage",
    ],
)
def test_invalid_tokens_are_rejected(client: TestClient, make_token: MakeToken, case: str) -> None:
    token = _bad_tokens(make_token)[case]
    res = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401
    assert res.json() == {"detail": "Invalid token"}


def test_hs256_token_signed_with_a_shared_secret_is_rejected(client: TestClient) -> None:
    # Algorithm-confusion guard: only asymmetric algorithms are accepted.
    import jwt

    token = jwt.encode({"sub": USER_ID, "exp": int(time.time()) + 60}, "x" * 32, algorithm="HS256")
    res = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401
