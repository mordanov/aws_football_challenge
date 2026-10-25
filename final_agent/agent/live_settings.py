"""Read-through S3 cache for live coach advice; refreshes every TTL seconds."""
from __future__ import annotations

import json
import time


class LiveSettingsCache:
    """Reads live coach JSON from S3 and caches it for TTL seconds.

    Returns {} on any error so decide() degrades gracefully.
    """

    def __init__(
        self,
        s3_client,
        bucket: str,
        match_id: str,
        prefix: str = "final-agent-learning/live-coach",
        ttl: float = 10.0,
    ):
        self._s3 = s3_client
        self._bucket = bucket
        self._key = f"{prefix}/{match_id}.json"
        self._settings: dict = {}
        self._last_check: float = 0.0
        self._ttl = ttl

    def get(self) -> dict:
        now = time.monotonic()
        if now - self._last_check < self._ttl:
            return self._settings
        self._last_check = now
        try:
            obj = self._s3.get_object(Bucket=self._bucket, Key=self._key)
            data = json.loads(obj["Body"].read())
            # Accept both {"changes": {...}} and flat {"rush_s": ...}
            self._settings = data.get("changes", data) if isinstance(data, dict) else {}
        except Exception:
            pass  # keep last known; S3 key may not exist yet
        return self._settings
