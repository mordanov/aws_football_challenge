"""Tests for cmd_decoder: wire commands → engine command format."""
from football_lab.cmd_decoder import decode_command


def test_move_to():
    wire = {"commandType": "MOVE_TO", "playerId": 2, "teamId": 0,
            "parameters": {"target_x": 10.0, "target_y": -5.0}, "duration": 0}
    cmd = decode_command(wire, team_id=0)
    assert cmd["command_type"] == "MOVE_TO"
    assert cmd["player_id"] == 2
    assert cmd["team_id"] == 0
    assert cmd["parameters"]["target_x"] == 10.0


def test_dribble_remaps():
    wire = {"commandType": "DRIBBLE", "playerId": 3, "teamId": 1,
            "parameters": {"x": 5.0, "y": -3.0}}
    cmd = decode_command(wire, team_id=1)
    assert cmd["command_type"] == "MOVE_TO"
    assert cmd["parameters"]["target_x"] == 5.0
    assert cmd["parameters"]["target_y"] == -3.0


def test_follow_player_remaps():
    wire = {"commandType": "FOLLOW_PLAYER", "playerId": 1, "teamId": 0,
            "parameters": {"target_player_id": 2, "target_team": 1, "distance": 1.0}}
    cmd = decode_command(wire, team_id=0)
    assert cmd["command_type"] == "MARK"
    assert cmd["parameters"]["target_player_id"] == 2
    assert "distance" not in cmd["parameters"]


def test_clear_remaps():
    wire = {"commandType": "CLEAR", "playerId": 4, "teamId": 0, "parameters": {}}
    cmd = decode_command(wire, team_id=0)
    assert cmd["command_type"] == "SET_STANCE"
    assert cmd["parameters"] == {"stance": 0}


def test_clear_override_remaps():
    wire = {"commandType": "CLEAR_OVERRIDE", "playerId": 0, "teamId": 1, "parameters": {}}
    cmd = decode_command(wire, team_id=1)
    assert cmd["command_type"] == "SET_STANCE"


def test_idle_remaps():
    cmd = decode_command({"commandType": "IDLE", "playerId": 0, "teamId": 0, "parameters": {}}, 0)
    assert cmd["command_type"] == "SET_STANCE"


def test_reset_remaps():
    cmd = decode_command({"commandType": "RESET", "playerId": 0, "teamId": 0, "parameters": {}}, 0)
    assert cmd["command_type"] == "SET_STANCE"


def test_team_id_injection():
    wire = {"commandType": "PASS", "playerId": 2, "teamId": 99,
            "parameters": {"target_player_id": 0, "type": "GROUND"}}
    cmd = decode_command(wire, team_id=1)
    assert cmd["team_id"] == 1


def test_pass_passes_through():
    wire = {"commandType": "PASS", "playerId": 1, "teamId": 0,
            "parameters": {"target_player_id": 3, "type": "AERIAL"}}
    cmd = decode_command(wire, team_id=0)
    assert cmd["command_type"] == "PASS"
    assert cmd["parameters"]["type"] == "AERIAL"


def test_legacy_xy_in_move_to():
    wire = {"commandType": "MOVE_TO", "playerId": 2, "teamId": 0,
            "parameters": {"x": 8.0, "y": -4.0}}
    cmd = decode_command(wire, team_id=0)
    assert cmd["parameters"]["target_x"] == 8.0
    assert cmd["parameters"]["target_y"] == -4.0
    assert "x" not in cmd["parameters"]
