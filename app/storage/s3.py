from contextlib import AsyncExitStack
from inspect import signature
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote

import aioboto3
from aiobotocore.config import AioConfig
from botocore.exceptions import ClientError

from app.core.config import settings
from app.storage.base import ObjectMeta, PresignedPost


class S3Storage:
    def __init__(self, client, signer, bucket: str):
        self._client = client
        self._signer = signer
        self._bucket = bucket

    @classmethod
    async def create(cls, stack: AsyncExitStack) -> "S3Storage":
        session = aioboto3.Session()
        config = AioConfig(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=3,
            read_timeout=30,
            retries={"max_attempts": 3, "mode": "standard"},
        )
        common = dict(
            region_name=settings.S3_REGION,
            awas_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
            config=config,
        )
        client = await stack.enter_async_context(
            session.client("s3", endpoint_url=settings.S3_ENDPOINT_URL, **common)
        )
        signer = await stack.enter_async_context(
            session.client(
                "s3",
                endpoint_url=settings.S3_PUBLIC_ENDPOINT_URL or settings.S3_ENDPOINT_URL,
                **common)
        )
        return cls(client, signer, settings.S3_BUCKET)

    async def upload_fileobj(self, key: str, fileobj: BinaryIO, *, content_type: str) -> None:
        await self._client.upload_fileobj(
            fileobj,
            self._bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )

    async def upload_file(self, key: str, path: Path, *, content_type: str) -> None:
        await self._client.upload_file(
            str(path),
            self._bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )

    async def head(self, key: str) -> ObjectMeta | None:
        try:
            resp = await self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
        return ObjectMeta(size=resp["ContentLength"], content_type=resp.get("ContentType", ""))

    async def read_head(self, key: str, size: int = 2048) -> bytes:
        resp = await self._client.get_object(Bucket=self._bucket, Key=key, Range=f"bytes=0-{size - 1}")
        async with resp["Body"] as body:
            return await body.read()

    async def delete(self, key: str) -> None:
        await self._client.delete_object(Bucket=self._bucket, Key=key)

    async def presign_get(self, key: str, *, expires: int, filename: str | None = None) -> str:
        params = {"Bucket": self._bucket, "key": key}
        if filename:
            params["ResponseContentDisposition"] = f"attachment;filename*=UTF-8''{quote(filename)}"
        return await self._signer.generate_presigned_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=expires,
        )

    async def presign_post(self, key: str, *, content_type: str, max_size: int, expires: int) -> PresignedPost:
        resp = await self._signer.generate_presigned_post(
            Bucket=self._bucket,
            Key=key,
            Fields={"Content-Type": content_type},
            Conditions=[{"Content-Type": content_type}, ["content-length-range", 1, max_size]],
            ExpiresIn=expires,
        )
        return PresignedPost(
            url=resp["url"],
            fields=resp["fields"],
        )

    async def ping(self) -> None:
        await self._client.head_bucket(Bucket=self._bucket)
