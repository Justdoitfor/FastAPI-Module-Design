# 工程化优化
## 当前存在的问题
```text
1. TTL 不应该到处写 86400
2. AuthService 不应该关心 Redis TTL 细节
3. TokenService 不应该依赖硬编码配置
4. Refresh Token 的创建、解析、存储职责要更加清晰
```
## config中增加属性 refresh_token_ttl
auth/core/config.py
```python
@property
def refresh_token_ttl(self) -> int:
    return int(self.REFRESH_TOKEN_EXPIRE_DAYS) * 24 * 60 * 60
```
## 替换对应的硬编码ttl
auth/services/token_service.py
```python
async def consume_refresh_token(
        self,
        jti: str,
) -> tuple[RefreshTokenStatus, RefreshTokenData | None]:
    key = self._refresh_key(jti)
    revoked_key = self._revoked_refresh_key(jti)
    script = """
    local value = redis.call('GET', KEYS[1])
    
    if value then
        redis.call(
            'DEL',
            KEYS[1]
        )
        redis.call(
            'SET',
            KEYS[2],
            value,
            'EX',
            ARGV[1]
        )
        return {1, value}
    end

    local revoked = redis.call(
        'GET',
        KEYS[2]
    )

    if revoked then
        return {2, revoked}
    end

    return {0, nil}
    """
    result = await redis_client.eval(
        script,
        2,
        key,
        revoked_key,
        settings.refresh_token_ttl,
    )
    status = result[0]

    if status == 1:
        data = json.loads(result[1])
        return (
            RefreshTokenStatus.VALID,
            RefreshTokenData(user_id=data["user_id"], family_id=data["family_id"])
        )

    if status == 2:
        data = json.loads(result[1])

        return (
            RefreshTokenStatus.REUSED,
            RefreshTokenData(user_id=data["user_id"], family_id=data["family_id"])
        )

    return (
        RefreshTokenStatus.INVALID,
        None
    )
```
auth/services/token_service.py
```python
async def refresh_access_token(
        self,
        refresh_token: str
):
    try:
        payload = verify_refresh_token(refresh_token)
    except ValueError:
        raise InvalidRefreshToken()
    old_jti = payload.get("jti")
    if not old_jti:
        raise InvalidRefreshToken()

    status, token_data = await self.token_service.consume_refresh_token(old_jti)
    if status == RefreshTokenStatus.REUSED:
        assert token_data is not None
        family_id = token_data.get("family_id")
        await self.token_service.revoke_family(family_id=family_id, ttl=settings.refresh_token_ttl)
        raise RefreshTokenReuseError()

    if status == RefreshTokenStatus.INVALID:
        raise InvalidRefreshToken()

    assert token_data is not None

    user_id = token_data.get("user_id")
    family_id = token_data.get("family_id")

    if await self.token_service.is_family_revoked(family_id=family_id):
        raise InvalidRefreshToken()

    user = await self.user_repository.get_by_id(int(user_id))
    if not user:
        raise UserNotExist()
    access_token = create_access_token(user.id)
    new_refresh_token = create_refresh_token(user_id=user.id, family_id=family_id)
    new_payload = decode_token(new_refresh_token)
    new_jti = new_payload.get("jti")
    new_exp = new_payload.get("exp")
    now = int(datetime.now(timezone.utc).timestamp())
    ttl = new_exp - now
    if ttl <= 0:
        raise InvalidRefreshToken()
    await self.token_service.register_refresh_token(
        jti=new_jti,
        user_id=user.id,
        family_id=family_id,
        ttl=ttl,
    )
    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
        token_type="bearer",
    )
```