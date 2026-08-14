# 账号退出功能
账号退出时， Refresh token立刻失效

## Logout设计
因为Access token生命周期较短，例如 30min  
所以，不需要删除Access Token  
**logout时只撤销Refresh Token**  
流程：
```text
Refresh Token
      ↓
JWT验证
      ↓
获取 jti
      ↓
Redis DEL
      ↓
Logout成功
```
之后：
```text
旧 Refresh Token
      ↓
POST /auth/refresh
      ↓
Redis不存在
      ↓
401
```

## token_service增加删除方法
auth/services/token_service.py
```python
async def revoke_refresh_token(
        self,
        jti: str,
) -> None:
    key = self._refresh_key(jti)
    await redis_client.delete(key)
```

## 增加logout schema
auth/schema/auth.py
```python
class LogoutRequest(BaseSchema):
    refresh_token: str
```

## AuthService 增加 Logout
auth/services/auth_service.py
```python
async def logout(
        self,
        refresh_token: str,
) -> None:
    try:
        payload = verify_refresh_token(refresh_token)
    except ValueError:
        raise InvalidRefreshToken()
    
    jti = payload.get("jti")
    if not jti:
        raise InvalidRefreshToken()
    
    await self.token_service.revoke_refresh_token(jti=jti)
```

## 增加 Router
auth/api/auth.py
```python
@router.post("/logout")
async def logout(
        data: LogoutRequest,
        service: AuthService = Depends(get_auth_service),
):
    await service.logout(
        data.refresh_token
    )
    return success()
```

## Logout 完整流程
```text
POST /api/v1/auth/logout
        │
        ↓
LogoutRequest
        │
        ↓
AuthService.logout()
        │
        ↓
verify_refresh_token()
        │
        ↓
获取 jti
        │
        ↓
TokenService.revoke_refresh_token()
        │
        ↓
Redis DEL
        │
        ↓
success
```