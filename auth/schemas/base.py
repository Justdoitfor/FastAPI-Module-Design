from pydantic import BaseModel, ConfigDict


class BaseSchema(BaseModel):
    """所有Schema 的基类"""
    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
        str_strip_whitespace=True,
    )
