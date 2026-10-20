"""Tiny key/value JSON store: S3 in production, memory in tests and locally.

Every S3 failure is swallowed and logged - learning must never break a match.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Optional, Protocol

log = logging.getLogger("afc.store")


class Store(Protocol):
    def get_json(self, key: str) -> Optional[Any]: ...
    def put_json(self, key: str, obj: Any) -> bool: ...
    def put_text(self, key: str, text: str) -> bool: ...
    def get_text(self, key: str) -> Optional[str]: ...
    def list(self, prefix: str) -> list[str]: ...


class MemoryStore:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self._lock = threading.Lock()

    def get_text(self, key: str) -> Optional[str]:
        with self._lock:
            return self.data.get(key)

    def put_text(self, key: str, text: str) -> bool:
        with self._lock:
            self.data[key] = text
        return True

    def get_json(self, key: str) -> Optional[Any]:
        t = self.get_text(key)
        try:
            return json.loads(t) if t is not None else None
        except ValueError:
            return None

    def put_json(self, key: str, obj: Any) -> bool:
        return self.put_text(key, json.dumps(obj, sort_keys=True))

    def list(self, prefix: str) -> list[str]:
        with self._lock:
            return sorted(k for k in self.data if k.startswith(prefix))


class S3Store:
    def __init__(self, bucket: str, prefix: str = "afc/") -> None:
        self.bucket, self.prefix = bucket, prefix
        self._s3 = None

    @property
    def s3(self):
        if self._s3 is None:
            import boto3  # lazy: keeps cold start and tests light
            from botocore.config import Config

            self._s3 = boto3.client("s3", config=Config(connect_timeout=2, read_timeout=5, retries={"max_attempts": 2}))
        return self._s3

    def get_text(self, key: str) -> Optional[str]:
        try:
            r = self.s3.get_object(Bucket=self.bucket, Key=self.prefix + key)
            return r["Body"].read().decode("utf-8")
        except Exception as e:  # noqa: BLE001
            if "NoSuchKey" not in repr(e):
                log.warning("s3 get %s failed: %r", key, e)
            return None

    def put_text(self, key: str, text: str) -> bool:
        try:
            self.s3.put_object(Bucket=self.bucket, Key=self.prefix + key, Body=text.encode("utf-8"))
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("s3 put %s failed: %r", key, e)
            return False

    def get_json(self, key: str) -> Optional[Any]:
        t = self.get_text(key)
        try:
            return json.loads(t) if t is not None else None
        except ValueError:
            return None

    def put_json(self, key: str, obj: Any) -> bool:
        return self.put_text(key, json.dumps(obj, sort_keys=True))

    def list(self, prefix: str) -> list[str]:
        out: list[str] = []
        try:
            pag = self.s3.get_paginator("list_objects_v2")
            for page in pag.paginate(Bucket=self.bucket, Prefix=self.prefix + prefix):
                out += [o["Key"][len(self.prefix):] for o in page.get("Contents", [])]
        except Exception as e:  # noqa: BLE001
            log.warning("s3 list %s failed: %r", prefix, e)
        return sorted(out)


def default_store() -> Store:
    bucket = os.environ.get("AFC_BUCKET")
    if bucket:
        return S3Store(bucket, os.environ.get("AFC_PREFIX", "afc/"))
    return MemoryStore()
