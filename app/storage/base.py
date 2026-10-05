from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol


@dataclass(frozen=True, slots=True)
class ObjectMeta:
    size: int
    content_type: str


@dataclass(frozen=True, slots=True)
class PresignedPost:
    url: str
    fields: dict[str, str]


class ObjectStorage(Protocol):
    async def upload_fileobj(self, key: str, fileobj: BinaryIO, *, content_type: str) -> None:
        pass

    async def upload_file(self, key: str, path: Path, *, content_type: str) -> None:
        pass

    async def head(self, key: str) -> ObjectMeta | None:
        pass

    async def read_head(self, key: str, size: int = 2048) -> bytes:
        pass

    async def delete(self, key: str) -> None:
        pass

    async def presign_get(self, key: str, *, expires: int, filename: str | None = None) -> str:
        pass

    async def presign_post(self, key: str, *, content_type: str, max_size: int, expires: int) -> PresignedPost:
        pass

    async def ping(self) -> None:
        pass


