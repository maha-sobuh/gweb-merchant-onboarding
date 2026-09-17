"""Thin S3 adapter for presigned uploads.

This is the ONLY module that may import boto3 for S3 (application_repo.py
remains the only module that may import boto3 for DynamoDB — keeping one
adapter per external service, per the "storage, external providers, AI,
and business policy are replaceable adapters" requirement in the spec).
"""

from __future__ import annotations

import os
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

PRESIGN_EXPIRY_SECONDS = 900  # 15 minutes — long enough for a slow upload,
# short enough that a leaked URL is not a standing liability.


class S3Client:
    def __init__(self, bucket_name: str | None = None, s3_resource: Any = None) -> None:
        self._bucket_name = bucket_name or os.environ["DOCUMENTS_BUCKET"]
        if s3_resource is None:
            kwargs: dict[str, Any] = {}
            region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
            if region:
                kwargs["region_name"] = region
            # Signature version 4 required for presigned URLs in every
            # region and is the only version that supports SSE headers.
            s3_resource = boto3.client("s3", config=Config(signature_version="s3v4"), **kwargs)
        self._client = s3_resource

    def generate_presigned_put_url(self, key: str, content_type: str) -> str:
        return self._client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self._bucket_name,
                "Key": key,
                "ContentType": content_type,
            },
            ExpiresIn=PRESIGN_EXPIRY_SECONDS,
        )

    def head_object(self, key: str) -> dict | None:
        """Returns {"size_bytes": int, "content_type": str} or None if the
        object does not exist (upload never completed / wrong key)."""
        try:
            response = self._client.head_object(Bucket=self._bucket_name, Key=key)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
        return {
            "size_bytes": response["ContentLength"],
            "content_type": response.get("ContentType", ""),
        }