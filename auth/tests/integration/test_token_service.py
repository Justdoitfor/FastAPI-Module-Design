import pytest

from auth.services.token_service import (
    TokenService,
    RefreshTokenStatus,
)


@pytest.mark.asyncio
async def test_register_and_consume_refresh_token():
    service = TokenService()

    jti = "test-jti-001"
    user_id = 1
    family_id = "family-001"

    await service.register_refresh_token(
        jti=jti,
        user_id=user_id,
        family_id=family_id,
        ttl=60,
    )

    status, result_data = (
        await service.consume_refresh_token(jti)
    )

    assert status == RefreshTokenStatus.VALID

    assert result_data is not None
    assert result_data["user_id"] == user_id
    assert result_data["family_id"] == family_id


@pytest.mark.asyncio
async def test_refresh_token_reuse():
    service = TokenService()

    jti = "test-jti-reuse"
    user_id = 1
    family_id = "family-reuse"

    await service.register_refresh_token(
        jti=jti,
        user_id=user_id,
        family_id=family_id,
        ttl=60,
    )

    first_status, first_data = (
        await service.consume_refresh_token(jti)
    )

    second_status, second_data = (
        await service.consume_refresh_token(jti)
    )

    assert first_status == RefreshTokenStatus.VALID
    assert first_data["user_id"] == user_id

    assert second_status == RefreshTokenStatus.REUSED
    assert second_data["user_id"] == user_id


@pytest.mark.asyncio
async def test_consume_invalid_refresh_token():
    service = TokenService()

    status, result_data = (
        await service.consume_refresh_token(
            "not-exists-jti"
        )
    )

    assert status == RefreshTokenStatus.INVALID
    assert result_data is None


@pytest.mark.asyncio
async def test_revoke_refresh_token():
    service = TokenService()

    jti = "test-jti-revoke"

    await service.register_refresh_token(
        jti=jti,
        user_id=1,
        family_id="family-revoke",
        ttl=60,
    )

    await service.revoke_refresh_token(jti)

    status, result_data = (
        await service.consume_refresh_token(jti)
    )

    assert status == RefreshTokenStatus.INVALID
    assert result_data is None


@pytest.mark.asyncio
async def test_revoke_family():
    service = TokenService()

    family_id = "family-test-001"

    await service.revoke_family(
        family_id=family_id,
        ttl=60,
    )

    result = await service.is_family_revoked(
        family_id
    )

    assert result is True


@pytest.mark.asyncio
async def test_family_not_revoked():
    service = TokenService()

    result = await service.is_family_revoked(
        "family-not-exists"
    )

    assert result is False
