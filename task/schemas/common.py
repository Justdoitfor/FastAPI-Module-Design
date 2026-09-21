import math
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar('T')


class PageParams(BaseModel):
    page: int = Field(1, ge=1, description="页码， 从 1 开始")
    size: int = Field(20, le=100, description="每页条数， 最大 100")

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    size: int
    pages: int

    @classmethod
    def build(cls, items: list, total: int, params: PageParams) -> "Page[T]":
        return cls(
            items=items,
            total=total,
            page=params.page,
            size=params.size,
            pages=math.ceil(total / params.size) if total else 0,
        )
