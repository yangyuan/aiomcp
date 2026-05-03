import time

import pytest

from aiomcp.mcp_authorization import McpAuthorizationServer


def test_authorization_server_jwt_round_trip():
    server = McpAuthorizationServer()
    now = int(time.time())

    token = server._encode_jwt(
        {
            "sub": "client-id",
            "scope": "mcp:tools",
            "exp": now + 60,
            "iat": now,
        }
    )

    claims = server._decode_jwt(token)

    assert claims["sub"] == "client-id"
    assert claims["scope"] == "mcp:tools"


def test_authorization_server_rejects_expired_jwt():
    server = McpAuthorizationServer()
    now = int(time.time())

    token = server._encode_jwt(
        {
            "sub": "client-id",
            "exp": now - 60,
            "iat": now - 120,
        }
    )

    with pytest.raises(Exception):
        server._decode_jwt(token)


def test_authorization_server_rejects_tampered_jwt():
    server = McpAuthorizationServer()
    now = int(time.time())

    token = server._encode_jwt(
        {
            "sub": "client-id",
            "exp": now + 60,
            "iat": now,
        }
    )
    header, payload, _ = token.split(".")
    tampered = f"{header}.{payload}.invalid-signature"

    with pytest.raises(Exception):
        server._decode_jwt(tampered)
