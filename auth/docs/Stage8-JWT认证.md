# JWT认证
JWT -JSON Web Token
一个经过签名的字符串
包含 header（加密算法）、Payload(业务数据）、Signature（签名）三部分
## 流程
```text
用户
 │
 ├── 注册 ─────────────→ PostgreSQL
 │
 └── 登录
       │
       ├── 校验用户名/密码
       │
       ├── 生成 Access Token
       │
       ├── 生成 Refresh Token
       │
       └── Refresh Token → Redis
                              │
                              └── jti → user_id + TTL


Access Token
 │
 └── 访问业务接口
       │
       └── JWT验证 → 获取 user_id → 查询用户


Access Token过期
 │
 └── Refresh Token
       │
       ├── JWT验证
       ├── type == refresh
       ├── 获取 jti
       ├── Redis验证
       ├── 获取 user_id
       ├── 查询用户
       └── 生成新的 Access Token
```

## payload核心字段
```json
{
    "sub": "1",
    "type": "access",
    "jti": "xxx",
    "iat": 1786628069,
    "exp": 1786629869
}
```
说明：
- sub  -> user_id
- type -> token 类型 
- jti  -> token 唯一id
- iat  -> 签发时间
- exp  -> 过期时间

## 注册流程
```text
POST /auth/register
        ↓
校验请求参数
        ↓
查询 username 是否存在
        ↓
查询 email 是否存在
        ↓
密码哈希
        ↓
创建 User
        ↓
保存 PostgreSQL
        ↓
返回注册结果
```
## 登录流程
```text
POST /auth/login
        ↓
查询用户
        ↓
验证密码
        ↓
检查用户状态
        ↓
生成 Access Token
        ↓
生成 Refresh Token
        ↓
解析 Refresh Token
        ↓
获取 jti + exp
        ↓
计算 TTL
        ↓
保存 Redis
        ↓
返回两个 Token
```

## access token
**用于访问业务资源**
### 验证流程
```text
Request
 ↓
Authorization Header
 ↓
提取 Bearer Token
 ↓
JWT Decode
 ↓
验证签名
 ↓
验证 exp
 ↓
验证 type == access
 ↓
获取 sub
 ↓
查询 User
 ↓
业务接口
```
## access token 和 refresh token隔离
- **业务接口只接受 access**
- **refresh接口只接受 refresh**
通过 payload中解析的type类型确定token类型为 access 还是 refresh

## refresh token刷新流程
```text
POST /auth/refresh
        ↓
提交 refresh_token
        ↓
JWT Decode
        ↓
验证签名
        ↓
验证 exp
        ↓
验证 type == refresh
        ↓
获取 jti
        ↓
Redis GET
        ↓
Token存在？
   ├── 否 → 401
   │
   └── 是
        ↓
     获取 user_id
        ↓
     查询 PostgreSQL
        ↓
     用户存在？
        ├── 否 → 404
        │
        └── 是
             ↓
        生成 Access Token
             ↓
          返回
```

## refresh token放在redis中
单纯的jwt：
```text
JWT有效
 ↓
服务器认为有效
```
问题：
```text
用户Logout
 ↓
JWT仍然没过期
 ↓
仍然可以Refresh
```

加入redis后：
```text
JWT有效
 +
Redis存在
 ↓
Token有效
```
服务端：
```text
Redis DEL
 ↓
Token立即失效
```
核心：**JWT负责无状态身份验证，Redis负责服务端状态控制**

## redis中保存的内容
不保存完整的refresh_token 而是**auth:refresh:{jti}**
例如：
```text
Key:
auth:refresh:51e21468-d23c-4b7c-934b-5caf55d6139b

Value:
1

TTL:
604800
```
## redis TTL不能固定
redis保存时间可能晚于JWT签发时间  

正确TTL：
```text
JWT exp
  ↓
当前时间
  ↓
exp - now
  ↓
Redis TTL
```
## 完整token生命周期
```text
                 Login
                   │
                   ↓
        ┌────────────────────┐
        │ Access + Refresh   │
        └─────────┬──────────┘
                  │
        ┌─────────┴──────────┐
        ↓                    ↓
 Access Token          Refresh Token
        │                    │
        ↓                    ↓
访问业务接口            Redis注册
        │                    │
        ↓                    ↓
    Access过期          /auth/refresh
                             │
                             ↓
                       Redis验证
                             │
                             ↓
                     新 Access Token
                             │
                             ↓
                       继续访问业务
                             
Logout
   ↓
Redis DEL
   ↓
Refresh Token失效
```

## JWT认证系统
```text
                JWT认证系统
                    │
          ┌─────────┴─────────┐
          ↓                   ↓
      Access Token        Refresh Token
          │                   │
       type=access         type=refresh
          │                   │
          ↓                   ↓
       业务接口              Redis
                              │
                              ↓
                       auth:refresh:jti
                              │
                              ↓
                           user_id
                              │
                              ↓
                          PostgreSQL
                              │
                              ↓
                         新Access Token


Login：
用户 → 密码校验 → Access + Refresh → Redis

Access：
Bearer Token → JWT验证 → User → 业务

Refresh：
Refresh Token → JWT验证 → Redis → User → 新Access

Logout：
Refresh Token → jti → Redis DEL → Token失效
```