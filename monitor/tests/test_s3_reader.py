import json
from datetime import datetime, timezone
from unittest.mock import MagicMock
import pytest

from monitor.s3_reader import S3Reader, RegistryMissing

BUCKET = "test-bucket"
TEAM = "test-team"
ACCOUNT = "123456789012"
D1 = "a" * 64
PREFIX = f"final-agent-learning/{TEAM}/"
TS = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _client(objects: dict) -> MagicMock:
    """objects: S3 key → body dict, or absent/None for NoSuchKey."""

    def get_object(Bucket, Key, ExpectedBucketOwner=None):
        body = objects.get(Key)
        if body is None:
            e = Exception("NoSuchKey")
            e.response = {"Error": {"Code": "NoSuchKey"}}
            raise e
        return {"Body": MagicMock(read=lambda: json.dumps(body).encode()), "LastModified": TS}

    def head_object(Bucket, Key, ExpectedBucketOwner=None):
        if objects.get(Key) is None:
            e = Exception("404")
            e.response = {"Error": {"Code": "404"}}
            raise e
        return {"LastModified": TS}

    c = MagicMock()
    c.get_object.side_effect = get_object
    c.head_object.side_effect = head_object
    return c


def _profile_doc(settings, version="v1"):
    return {
        "profile": {
            "version": version,
            "settings": settings,
            "style": {},
            "coach_policy": None,
            "opponents": {},
        }
    }


# ---------------------------------------------------------------------------
# Task 1: current_profile()
# ---------------------------------------------------------------------------

def test_current_profile_basic():
    active = {
        "digest": D1,
        "generation": 2,
        "predecessor": None,
        "receipt": None,
        "commit": PREFIX + f"registry/generations/{D1}.json",
    }
    profile = _profile_doc(
        {"press_radius": 18.0, "pass_margin": 3.0, "shot_range": 32.0, "support_width": 15.0},
        version="auto_abc",
    )
    client = _client(
        {
            PREFIX + "registry/ACTIVE.json": active,
            PREFIX + f"registry/profiles/{D1}.json": profile,
            PREFIX + f"registry/generations/{D1}.json": {"digest": D1, "generation": 2},
        }
    )
    r = S3Reader(client, BUCKET, TEAM, ACCOUNT).current_profile()
    assert r["version"] == "auto_abc"
    assert r["generation"] == 2
    assert r["digest_short"] == D1[:8]
    assert r["settings"]["press_radius"] == 18.0
    assert r["settings"]["pass_margin"] == 3.0
    assert r["settings"]["shot_range"] == 32.0
    assert r["settings"]["support_width"] == 15.0
    assert "active_since" in r


def test_current_profile_registry_missing():
    client = _client({})
    with pytest.raises(RegistryMissing):
        S3Reader(client, BUCKET, TEAM, ACCOUNT).current_profile()
