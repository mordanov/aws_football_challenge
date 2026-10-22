"""Tests for obs_encoder: engine EngineState → AWS gameState payload."""
import pytest

from football_lab.obs_encoder import encode_observation


def _make_state(tick: int = 0, play_mode: str = "OPEN_PLAY") -> dict:
    players = []
    for pid in range(5):
        players.append({
            "team_id": 0,
            "player_id": pid,
            "x": -40.0 + pid,
            "y": -20.0 + pid,
            "vx": 0.0,
            "vy": 0.0,
            "stamina": 1.0,
        })
        players.append({
            "team_id": 1,
            "player_id": pid,
            "x": 40.0 - pid,
            "y": 20.0 - pid,
            "vx": 0.0,
            "vy": 0.0,
            "stamina": 1.0,
        })
    return {
        "tick": tick,
        "play_mode": play_mode,
        "mode_team": None,
        "players": players,
        "ball": {
            "x": 0.0, "y": 0.0, "z": 0.0,
            "vx": 0.0, "vy": 0.0, "vz": 0.0,
            "owner_team": None, "owner_player": None,
            "in_flight": False,
        },
        "score": [0, 0],
    }


def test_player_positions():
    state = _make_state()
    obs = encode_observation(state, team_id=0, player_ids=[0], seed=42)
    p = next(p for p in obs["gameState"]["players"]
             if p["playerId"] == 0 and p["teamId"] == 0)
    assert p["position"]["x"] == -40.0
    assert p["position"]["y"] == -20.0


def test_game_time():
    state = _make_state(tick=40)
    obs = encode_observation(state, team_id=0, player_ids=[0], seed=0)
    assert obs["gameState"]["gameTime"] == pytest.approx(2.0)


def test_free_ball():
    state = _make_state()
    obs = encode_observation(state, team_id=1, player_ids=[0], seed=0)
    b = obs["gameState"]["ball"]
    assert b["isFree"] is True
    assert b["possessionPlayerId"] is None
    assert b["possessionTeamId"] is None


def test_owned_ball():
    state = _make_state()
    state["ball"]["owner_team"] = 1
    state["ball"]["owner_player"] = 3
    obs = encode_observation(state, team_id=1, player_ids=[3], seed=0)
    b = obs["gameState"]["ball"]
    assert b["isFree"] is False
    assert b["possessionPlayerId"] == 3
    assert b["possessionTeamId"] == 1


def test_score():
    state = _make_state()
    state["score"] = [2, 1]
    obs = encode_observation(state, team_id=0, player_ids=[0], seed=0)
    assert obs["gameState"]["score"] == {"home": 2, "away": 1}


def test_envelope_fields():
    obs = encode_observation(_make_state(), team_id=1, player_ids=[2, 3], seed=7)
    assert obs["teamId"] == 1
    assert obs["myPlayers"] == [2, 3]


def test_play_mode():
    state = _make_state(play_mode="CORNER")
    obs = encode_observation(state, team_id=0, player_ids=[0], seed=0)
    assert obs["gameState"]["playMode"] == "CORNER"


def test_all_players():
    obs = encode_observation(_make_state(), team_id=0, player_ids=[0], seed=0)
    assert len(obs["gameState"]["players"]) == 10
