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
    contents = [
        {"Key": OBS_PREFIX + "2026-10-24/match-aaa/0/obs1.json"},
        {"Key": OBS_PREFIX + "2026-10-24/match-bbb/0/obs2.json"},
        {"Key": OBS_PREFIX + "2026-10-23/match-aaa/0/obs3.json"},
    ]
    client = MagicMock()
    client.list_objects_v2.return_value = {"Contents": contents, "IsTruncated": False}
    result = S3Reader(client, BUCKET, TEAM, ACCOUNT).match_activity(days=3)
    by_date = {r["date"]: r["match_count"] for r in result}
    # Two distinct matches on 2026-10-24, one on 2026-10-23
    assert by_date.get("2026-10-24", 0) == 2
    assert by_date.get("2026-10-23", 0) == 1


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
