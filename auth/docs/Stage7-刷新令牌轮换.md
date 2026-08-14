# Refresh Token Rotation——刷新令牌轮换及进阶family_id实现
问题：攻击者可能通过攻击获取Refresh Token，如果没有轮换，攻击者可通过Refresh Token不断生成Access token  
核心思想：  
```text
Refresh Token A
        ↓
     立即失效
        ↓
Refresh Token B
```
每一次 Refresh 都产生一个新的 Refresh Token  
## redis 状态变化
第一次登录
```text
auth:refresh:AAA -> user_id=1
```
使用A：
```text
Refresh A
```
刷新后：
```text
删除：
auth:refresh:AAA

新增：
auth:refresh:BBB → user_id=1
```
## 修改 TokenService
auth/services/token_service.py
```python
async def rotate_refresh_token(
        self,
        olf_jti: str,
        new_jti: str,
        user_id: int,
        ttl: int,
) -> None:
    old_key = self._refresh_key(olf_jti)
    new_key = self._refresh_key(new_jti)
    
    async with redis_client.pipeline(transaction=True) as pipeline:
        pipeline.delete(old_key)
        pipeline.set(new_key, str(user_id), ex=ttl)
        await pipeline.execute()
```
这里使用pipline的原因？  
如果使用下面的方式：
```python
await redis_client.delete(old_key)

await redis_client.set(
    new_key,
    str(user_id),
    ex=ttl,
)
```
中间会存在一个时间窗口：
```text
DELETE成功
       ↓
程序异常
       ↓
SET没有执行(核心问题，set之前可能出现问题)
```
导致：
```text
旧Token ❌
新Token ❌
```
即 用户被 “登出”  
使用 pipline 可以将两个操作放在一起执行：
```text
DELETE old
+
SET new
 ↓
EXEC
```
但是，使用pipline依然存在问题——旧的Token被重复使用：
```text
请求A：Refresh A
请求B：Refresh A
```
两个请求几乎同时到达,两个请求都看到A存在，产生并发竞争  
因此，严谨的Rotation需要**原子地检查并删除旧的Token**

## 使用Lua实现原子消费
auth/services/token_service.py
```python
async def consume_refresh_token(
        self,
        jti: str,
) -> int | None:
    key = self._refresh_key(jti)
    script = """
    local value = redis.call("GET", KEYS[1])
    
    if not value then
        return nil
    end
    
    redis.call("DEL", KEYS[1])
    return value
    """
    user_id = await redis_client.eval(
        script,
        1,
        key,
    )
    if user_id is None:
        return None
    return int(user_id)
```
Redis 执行 Lua Script时：
```text
脚本开始
 ↓
其他Redis命令等待
 ↓
脚本执行完成
 ↓
其他命令继续
```
## 修改refresh流程
auth/services/auth_service.py
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

    user_id = await self.token_service.consume_refresh_token(old_jti)
    if not user_id:
        raise InvalidRefreshToken()

    user = await self.user_repository.get_by_id(int(user_id))
    if not user:
        raise UserNotExist()
    access_token = create_access_token(user.id)
    new_refresh_token = create_refresh_token(user.id)
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
        ttl=ttl,
    )
    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
        token_type="bearer",
    )
```
此时已经不再需要rotate_refresh_token():
```text
旧 Token
 ↓
consume_refresh_token()
 ↓
已经被原子删除

新 Token
 ↓
register_refresh_token()
```

## Token Reuse问题
已经失效的token被再次使用，表明Refresh Token泄露

### 引入 Token Family
增加family_id
```text
A
jti = AAA
family_id = F001
```
刷新后：
```text
A -> B

B
jti = BBB
family_id = F001
```
### 为什么需要 Family？

假设：
A → B → C
攻击者拿到了 B
用户正常使用：
B → C
攻击者后来使用 B
系统发现： B 已经被消费
这时候说明： B 很可能泄露
最安全的处理方式：
撤销整个 Family

也就是：
A ❌
B ❌
C ❌

**要求用户重新登录**

## 增加Token状态 key
auth/services/token_service.py
```python
REVOKED_REFRESH_TOKEN_PREFIX = "auth:refresh:revoked"

@classmethod
def _revoked_refresh_key(cls, jti: str) -> str:
    return f"{cls.REVOKED_REFRESH_TOKEN_PREFIX}:{jti}"
```
修改 consume_refresh_token()
```python
async def consume_refresh_token(
        self,
        jti: str,
) -> tuple[RefreshTokenStatus, int | None]:
    key = self._refresh_key(jti)
    revoked_key = self._revoked_refresh_key(jti)
    script = """
    local value = redis.call('GET', KEYS[1])
    
    if value then
        redis.call('DEL', KEYS[1])
        redis.call('SET', KEYS[2], '1', 'EX', ARGV[1])
        return {1, value}
    end
    
    local revoked = redis.call('GET', KEYS[2])
    if revoked then
        return {2, nil}
    end
    
    return {0, nil}
    """
    result = await redis_client.eval(
        script,
        2,
        key,
        revoked_key,
        86400,
    )
    status = result[0]

    if status == 1:
        return RefreshTokenStatus.VALID, int(result[1])

    if status == 2:
        return RefreshTokenStatus.REUSED, None

    return RefreshTokenStatus.INVALID, None
```

## AuthService 处理三种状态
auth/services/auth_service.py
```python
status, user_id = await self.token_service.consume_refresh_token(old_jti)
if status == RefreshTokenStatus.REUSED:
    raise RefreshTokenReuseError()

if status == RefreshTokenStatus.INVALID:
    raise InvalidRefreshToken()

assert user_id is not None
```

## 增加错误码
auth/core/error_codes.py
```python
Refresh_Token_Reuse = (
        11003,
        "refresh token reuse",
        401
    )
```

## 增加exceptions
auth/core/exceptions.py
```python
class RefreshTokenReuseError(BusinessException):
    def __init__(self):
        super().__init__(ErrorCode.Refresh_Token_Reuse)
```
## 进阶实现，如果触发reuse后，所有登录状态立即失效
###  TokenService 增加 Family Key
auth/services/token_service.py
```python
FAMILY_PREFIX = "auth:family"

@classmethod
def _family_key(cls, family_id: str) -> str:
    return f"{cls.FAMILY_PREFIX}:{family_id}"

async def consume_refresh_token(
        self,
        jti: str,
) -> tuple[RefreshTokenStatus, RefreshTokenData | None]:
    key = self._refresh_key(jti)
    revoked_key = self._revoked_refresh_key(jti)
    script = """
    local value = redis.call('GET', KEYS[1])
    
    if value then
        redis.call('DEL', KEYS[1])
        redis.call('SET', KEYS[2], value, 'EX', ARGV[1])
        return {1, value}
    end
    
    local revoked = redis.call('GET', KEYS[2])
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
        86400,
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

    async def revoke_family(
            self,
            family_id: str,
            ttl: int,
    ) -> None:
        key = self._family_key(family_id)
        await redis_client.set(
            key,
            '1',
            ex=ttl,
        )
```

### 修改AuthService
auth/services/auth_service.py
```python
if status == RefreshTokenStatus.REUSED:
    assert token_data is not None
    family_id = token_data.get("family_id")
    await self.token_service.revoke_family(family_id=family_id, ttl=86400)
    raise RefreshTokenReuseError()
```

### TokenService 增加is_family_revoked()
auth/services/token_service.py
````python
async def is_family_revoked(
        self,
        family_id: str,
) -> bool:
    key = self._family_key(family_id)
    result = await redis_client.get(key)
    return result is not None
````
### 修改 AuthService.refresh_access_token()
auth/services/auth_service.py
```python
if status == RefreshTokenStatus.REUSED:
    assert token_data is not None
    family_id = token_data.get("family_id")
    await self.token_service.revoke_family(family_id=family_id, ttl=86400)
    raise RefreshTokenReuseError()

if status == RefreshTokenStatus.INVALID:
    raise InvalidRefreshToken()

assert token_data is not None

user_id = token_data.get("user_id")
family_id = token_data.get("family_id")

if await self.token_service.is_family_revoked(family_id=family_id):
    raise InvalidRefreshToken()
```