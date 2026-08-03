# 异常体系测试/验证

当前异常体系：

```text
ErrorCode
    ↓
BusinessException
    ↓
Exception Handler
    ↓
统一Response
```

但是工程开发中：

> 写完代码 ≠ 完成功能

必须验证：

* 异常是否被正确捕获
* HTTP 状态码是否正确
* 返回格式是否统一
* 是否泄露内部异常信息

---

## 一、测试前检查项目结构

当前应该类似：

```text
app

├── api
│   └── auth.py
│
├── core
│   ├── error_codes.py
│   ├── exceptions.py
│   ├── exception_handlers.py
│   └── response.py
│
├── schemas
│   ├── response.py
│   └── auth.py
│
├── services
│   └── auth_service.py
│
└── main.py
```

---

## 二、确认 main.py 注册异常

```text
app/main.py
```
确认：

```python
from fastapi import FastAPI
from auth.database.session import engine
from auth.api import auth
from auth.core.exceptions import BusinessException
from auth.core.exception_handler import (
    business_exception_handler,
    validation_exception_handler,
    http_exception_handler,
    database_exception_handler,
    global_exception_handler,
)
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import IntegrityError

app = FastAPI(
    title="Auth System",
    version="1.0.0",
)
app.include_router(auth.router, prefix="/api/v1")
app.add_exception_handler(BusinessException, business_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(IntegrityError, database_exception_handler)
app.add_exception_handler(Exception, global_exception_handler)


@app.get("/")
async def root():
    return {"message": "Server Running..."}


@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        print("database connected!")

```

---

## 三、测试1：正常注册成功

### 请求

Swagger：

```
POST /api/v1/auth/register
```

参数：

```json
{
  "username": "user01",
  "email": "user01@test.com",
  "password": "12345678"
}
```

---

### 预期

HTTP:

```http
200 OK
```

返回：

```json
{
    "code":0,
    "message":"success",
    "data":{
        "id":1,
        "username":"user01",
        "email":"user01@test.com"
    }
}
```

---

如果这里失败：

优先检查：

* Service返回对象
* ResponseModel泛型
* Pydantic ORM转换

---

## 四、测试2：用户名重复异常

再次请求：

```json
{
  "username": "user01",
  "email": "another@test.com",
  "password": "12345678"
}
```

---

流程：

```text
AuthService

↓

发现用户名存在

↓

raise UsernameAlreadyExists()

↓

BusinessException Handler

```

---

预期：

HTTP:

```http
409 Conflict
```

Body:

```json
{
    "code":10001,
    "message":"username already exists",
    "data":null
}
```

---

如果返回：

```json
{
    "detail":"username already exists"
}
```

说明：

异常Handler没有注册成功。

---

## 五、测试3：邮箱重复

请求：

```json
{
  "username":"user02",
  "email":"user01@test.com",
  "password":"12345678"
}
```

---

预期：

```http
409
```

返回：

```json
{
    "code":10002,
    "message":"email already exists",
    "data":null
}
```

---

## 六、测试4：参数校验异常

现在测试 Pydantic。

例如：

发送：

```json
{
    "username":"",
    "email":"abc",
    "password":"1"
}
```

Schema：

```python
username:
    min_length=3

password:
    min_length=8
```

FastAPI：

自动触发：

```python
RequestValidationError
```

---

预期：

HTTP:

```http
422
```

返回：

```json
{
    "code":40001,
    "message":"invalid parameters",
    "data":null
}
```

---

注意：

这里我们主动隐藏了：

```python
exc.errors()
```

原因：

生产环境不希望暴露过多校验细节。

后续可以优化成：

```json
{
    "code":40001,
    "message":"invalid parameters",
    "data":{
        "username":"长度不足"
    }
}
```

---

## 七、测试5：未知异常

这是测试兜底。

临时修改：

```python
auth/services/auth_service.py
```

注册方法：

加入：

```python
raise Exception(
    "test error"
)
```

例如：

```python
async def register(...):

    raise Exception(
        "test error"
    )
```

---

请求注册。

---

预期：

HTTP:

```http
500
```

返回：

```json
{
    "code":50000,
    "message":"internal server error",
    "data":null
}
```

---

注意：

不能返回：

```json
{
 "message":"test error"
}
```

因为：

线上可能暴露：

* 数据库信息
* 文件路径
* 代码结构

---

测试完成后删除：

```python
raise Exception()
```

---

## 八、测试6：数据库异常

这个测试稍微特殊。

例如：

直接绕过 Service：

连续插入：

```python
User(
    username="test"
)
```

两次。

第二次：

数据库：

```text
unique violation
```

SQLAlchemy：

```python
IntegrityError
```

---

预期：

```http
500
```

返回：

```json
{
    "code":50001,
    "message":"database conflict",
    "data":null
}
```

---

不过这里有一个问题：

目前：

```python
database_exception_handler
```

太粗。

真实项目下一步会优化：

根据 PostgreSQL错误：

```text
users_username_key
users_email_key
```

转换：

```text
10001 用户名重复

10002 邮箱重复
```

---

# 九、使用 pytest 自动化测试（下一阶段铺垫）

目前手动 Swagger 测试：

适合开发阶段。

企业：

需要：

```text
pytest

↓

TestClient

↓

模拟请求

↓

验证结果
```

例如：

```python
def test_register_success():

    response = client.post(
        "/api/v1/auth/register",
        json=data
    )

    assert response.status_code == 200
```


