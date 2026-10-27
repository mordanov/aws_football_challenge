import json
from unittest.mock import MagicMock, patch
import pytest

BUCKET = "b"
TEAM = "t"
ACCOUNT = "1" * 12
OBS_PREFIX = f"final-agent-learning/{TEAM}/observations/_days/"


# ---------------------------------------------------------------------------
# match_activity() — on S3Reader directly
# ---------------------------------------------------------------------------

from monitor.s3_reader import S3Reader


def test_match_activity_empty():
    client = MagicMock()
    client.list_objects_v2.return_value = {"Contents": [], "IsTruncated": False}
    result = S3Reader(client, BUCKET, TEAM, ACCOUNT).match_activity(days=3)
    assert len(result) == 3
    assert all(r["match_count"] == 0 for r in result)
    # oldest first
    assert result[0]["date"] < result[-1]["date"]


def test_match_activity_counts_distinct_matches():
    from datetime import date, timedelta
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    # list_objects_v2 with Delimiter="/" returns CommonPrefixes for virtual dirs.
    # First call: enumerate date prefixes. Second+ calls: enumerate match IDs per date.
    def _list(Bucket, Prefix, Delimiter, **_):
        if Prefix == OBS_PREFIX:
            return {"CommonPrefixes": [
                {"Prefix": OBS_PREFIX + today + "/"},
                {"Prefix": OBS_PREFIX + yesterday + "/"},
            ], "IsTruncated": False}
        if Prefix == OBS_PREFIX + today + "/":
            return {"CommonPrefixes": [
                {"Prefix": OBS_PREFIX + today + "/match-aaa/"},
                {"Prefix": OBS_PREFIX + today + "/match-bbb/"},
            ], "IsTruncated": False}
        if Prefix == OBS_PREFIX + yesterday + "/":
            return {"CommonPrefixes": [
                {"Prefix": OBS_PREFIX + yesterday + "/match-aaa/"},
            ], "IsTruncated": False}
        return {"IsTruncated": False}

    client = MagicMock()
    client.list_objects_v2.side_effect = _list
    result = S3Reader(client, BUCKET, TEAM, ACCOUNT).match_activity(days=3)
    by_date = {r["date"]: r["match_count"] for r in result}
    assert by_date.get(today, 0) == 2
    assert by_date.get(yesterday, 0) == 1


# ---------------------------------------------------------------------------
# Flask /api/status
# ---------------------------------------------------------------------------

from monitor.monitor import create_app


@pytest.fixture
def mock_reader():
    r = MagicMock()
    r.current_profile.return_value = {
        "version": "v1",
        "generation": 1,
        "digest_short": "abcd1234",
        "active_since": "2026-10-01T00:00:00+00:00",
        "settings": {
            "press_radius": 20.0,
            "pass_margin": 3.0,
            "shot_range": 32.0,
            "support_width": 15.0,
        },
    }
    r.promotion_history.return_value = []
    r.match_activity.return_value = [{"date": "2026-10-24", "match_count": 0}]
    return r


def test_api_status_shape(mock_reader):
    app = create_app(BUCKET, TEAM, ACCOUNT)
    with patch("monitor.monitor.S3Reader", return_value=mock_reader):
        resp = app.test_client().get("/api/status")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "current_profile" in data
    assert "promotion_history" in data
    assert "match_activity" in data
    assert "fetched_at" in data
    assert data["team_id"] == TEAM


def test_api_status_s3_error():
    app = create_app(BUCKET, TEAM, ACCOUNT)
    with patch("monitor.monitor.S3Reader") as Cls:
        Cls.return_value.current_profile.side_effect = Exception("S3 unavailable")
        resp = app.test_client().get("/api/status")
    assert resp.status_code == 500
    assert "error" in json.loads(resp.data)


def test_api_status_registry_missing_still_returns_match_activity(mock_reader):
    """RegistryMissing on current_profile must not block match_activity."""
    from monitor.s3_reader import RegistryMissing
    mock_reader.current_profile.side_effect = RegistryMissing("not bootstrapped")
    mock_reader.promotion_history.side_effect = RegistryMissing("not bootstrapped")
    app = create_app(BUCKET, TEAM, ACCOUNT)
    with patch("monitor.monitor.S3Reader", return_value=mock_reader):
        resp = app.test_client().get("/api/status")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "error" in data["current_profile"]
    assert data["promotion_history"] == []
    assert data["match_activity"] == [{"date": "2026-10-24", "match_count": 0}]
