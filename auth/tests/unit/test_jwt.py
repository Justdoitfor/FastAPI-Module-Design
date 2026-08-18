import pytest

from auth.core.jwt import (
    create_access_token,
    decode_token,
    create_refresh_token,
    verify_refresh_token,
)


def test_create_access_token():
    token = create_access_token(1)
    payload = decode_token(token)

    assert payload["sub"] == "1"
    assert payload["type"] == "access"
    assert payload["jti"]
    assert payload["iat"]
    assert payload["exp"]


def test_create_refresh_token():
    family_id = "family-001"

    token = create_refresh_token(
        user_id=1,
        family_id=family_id,
    )

    payload = decode_token(token)

    assert payload["sub"] == "1"
    assert payload["type"] == "refresh"
    assert payload["family_id"] == family_id
    assert payload["jti"]
    assert payload["iat"]
    assert payload["exp"]


def test_access_token_should_not_have_family_id():
    token = create_access_token(1)

    payload = decode_token(token)

    assert "family_id" not in payload


def test_verify_refresh_token():
    token = create_refresh_token(
        user_id=1,
        family_id="family-001",
    )

    payload = verify_refresh_token(token)

    assert payload["sub"] == "1"
    assert payload["type"] == "refresh"
    assert payload["family_id"] == "family-001"


def test_access_token_cannot_be_used_as_refresh_token():
    token = create_access_token(1)

    with pytest.raises(ValueError):
        verify_refresh_token(token)


def test_token_jti_should_be_unique():
    token1 = create_access_token(1)
    token2 = create_access_token(1)

    payload1 = decode_token(token1)
    payload2 = decode_token(token2)

    assert payload1["jti"] != payload2["jti"]


def test_refresh_tokens_should_share_same_family():
    family_id = "family-001"

    token1 = create_refresh_token(
        user_id=1,
        family_id=family_id,
    )

    token2 = create_refresh_token(
        user_id=1,
        family_id=family_id,
    )

    payload1 = decode_token(token1)
    payload2 = decode_token(token2)

    assert payload1["family_id"] == payload2["family_id"]
    assert payload1["jti"] != payload2["jti"]
