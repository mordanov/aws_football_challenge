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


# ---------------------------------------------------------------------------
# Task 2: promotion_history()
# ---------------------------------------------------------------------------

D2 = "b" * 64


def test_promotion_history_single_gen_no_attempt():
    """Gen 1, no predecessor, no results file → no_attempt, empty changes."""
    active = {
        "digest": D1,
        "generation": 1,
        "predecessor": None,
        "receipt": None,
        "commit": PREFIX + f"registry/generations/{D1}.json",
    }
    gen1 = {"digest": D1, "generation": 1, "predecessor": None}
    profile1 = _profile_doc(
        {"press_radius": 22.0, "pass_margin": 3.0, "shot_range": 32.0, "support_width": 15.0}
    )
    client = _client(
        {
            PREFIX + "registry/ACTIVE.json": active,
            PREFIX + f"registry/profiles/{D1}.json": profile1,
            PREFIX + f"registry/generations/{D1}.json": gen1,
        }
    )
    history = S3Reader(client, BUCKET, TEAM, ACCOUNT).promotion_history()
    assert len(history) == 1
    assert history[0]["generation"] == 1
    assert history[0]["changes"] == {}
    assert history[0]["status"] == "no_attempt"
    assert history[0]["reject_reason"] is None


def test_promotion_history_two_gens_rejected():
    """Gen 2 is active (promoted); gen 1 attempt was rejected with reason."""
    EVAL = "c" * 64
    gen1 = {"digest": D1, "generation": 1, "predecessor": None}
    gen2 = {
        "digest": D2,
        "generation": 2,
        "predecessor": PREFIX + f"registry/generations/{D1}.json",
    }
    active = {
        "digest": D2,
        "generation": 2,
        "predecessor": PREFIX + f"registry/generations/{D1}.json",
        "commit": PREFIX + f"registry/generations/{D2}.json",
    }
    profile1 = _profile_doc(
        {"press_radius": 22.0, "pass_margin": 3.0, "shot_range": 32.0, "support_width": 15.0}
    )
    profile2 = _profile_doc(
        {"press_radius": 20.0, "pass_margin": 3.0, "shot_range": 32.0, "support_width": 15.0}
    )
    result1 = {
        "status": "rejected",
        "generation": 1,
        "evaluation_ref": PREFIX + f"registry/evaluations/{EVAL}.json",
    }
    eval1 = {"accepted": False, "reason": "nonpositive_mean_delta"}
    client = _client(
        {
            PREFIX + "registry/ACTIVE.json": active,
            PREFIX + f"registry/profiles/{D1}.json": profile1,
            PREFIX + f"registry/profiles/{D2}.json": profile2,
            PREFIX + f"registry/generations/{D1}.json": gen1,
            PREFIX + f"registry/generations/{D2}.json": gen2,
            PREFIX + "registry/results/1.json": result1,
            PREFIX + f"registry/evaluations/{EVAL}.json": eval1,
        }
    )
    history = S3Reader(client, BUCKET, TEAM, ACCOUNT).promotion_history()
    assert len(history) == 2
    # newest first
    assert history[0]["generation"] == 2
    assert history[0]["status"] == "promoted"
    # gen 2 changes show diff vs its predecessor gen 1 (press_radius: 22 → 20)
    assert history[0]["changes"] == {"press_radius": {"before": 22.0, "after": 20.0}}
    assert history[1]["generation"] == 1
    assert history[1]["status"] == "rejected"
    assert history[1]["reject_reason"] == "nonpositive_mean_delta"
    # gen 1 has no predecessor, so no changes
    assert history[1]["changes"] == {}


def test_promotion_history_cap_at_50():
    """Chain longer than 50 generations is truncated to 50 entries."""
    digests = [str(i).zfill(64) for i in range(51)]
    objects = {}
    for i in range(51):
        d = digests[i]
        pred = PREFIX + f"registry/generations/{digests[i - 1]}.json" if i > 0 else None
        objects[PREFIX + f"registry/generations/{d}.json"] = {
            "digest": d,
            "generation": i + 1,
            "predecessor": pred,
        }
        objects[PREFIX + f"registry/profiles/{d}.json"] = _profile_doc(
            {"press_radius": 22.0, "pass_margin": 3.0, "shot_range": 32.0, "support_width": 15.0}
        )
    active_d = digests[50]
    objects[PREFIX + "registry/ACTIVE.json"] = {
        "digest": active_d,
        "generation": 51,
        "predecessor": None,
        "commit": PREFIX + f"registry/generations/{active_d}.json",
    }
    client = _client(objects)
    history = S3Reader(client, BUCKET, TEAM, ACCOUNT).promotion_history()
    assert len(history) == 50
