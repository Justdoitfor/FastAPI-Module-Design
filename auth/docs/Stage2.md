# 用户注册功能开发
## 首先，需要明确业务流程
```text
用户点击注册
↓
接收到请求
↓
校验参数
↓
用户名是否存在？
↓
邮箱是否存在？
↓
密码加密

创建用户
↓
返回用户信息
```
## 第一步：设计Schema（接口契约）
前后端通过接口通信，需要明确接口数据字段相关信息

### 注册请求
```json
{
  "username": "admin",
  "email": "admin@test.com",
  "password": "123456"
}
```
### 对应的数据类——Schema
```python
class UserRegisterRequest(BaseModel):
    username: str
    email: EmailStr # Pydantic会自动验证是否为邮箱类型
    password: str   # Pydantic没有密码类型，企业通常使用 password: Annotated[str, Field(min_length=8, max_length=64)]
```
- 字段的约束都是Schema层负责

## 第二步：设计响应
响应的信息中不会包含密码信息，因此需要设计单独的响应模型Schema
例如：
```python
class UserResponse(BaseModel):
    id: int
    username: str
    email: EmailStr
```

## 第三步：设计Repository
Repository只负责数据库部分, 不会进行合法性校验或者JWT生成
```python
class UserRepository:

    async def get_by_username(...):
        ...

    async def get_by_email(...):
        ...

    async def create(...):
        ...
```
**Repository 应该返回 ORM 对象**

## 第四步：设计Service
真正的业务逻辑（此处为注册）在这里实现
业务流程组织：
```text
register()
↓
查询用户名
↓
存在？
   ↓
 抛异常
↓
查询邮箱
↓
存在？
   ↓
 抛异常
↓
密码加密
↓
Repository.create()
↓
返回用户
```

## 第五步：Router——路由
```python
@router.post("/register")
async def register(
    request: UserRegisterRequest,
    service: AuthService = Depends(get_auth_service),
):
    return await service.register(request)
```

