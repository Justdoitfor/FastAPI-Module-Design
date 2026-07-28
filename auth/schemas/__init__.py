"""
负责接口数据模型
例如：
{
 username:"",
 password:""
}
---------------->
对应：
class UserCreate(BaseModel):
    username:str
    password:str
"""