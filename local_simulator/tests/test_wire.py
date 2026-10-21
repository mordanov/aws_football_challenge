import unittest

from football_lab.wire import parse_reply


def reply(team=0):
    return {
        "schema_version": 1,
        "commands": [
            {
                "team_id": team,
                "player_id": player,
                "command_type": "MOVE_TO",
                "target_x": 0.0,
                "target_y": 0.0,
            }
            for player in range(5)
        ],
    }


class WireTests(unittest.TestCase):
    def test_rejects_wrong_owner_and_duplicate_player(self):
        with self.assertRaises(ValueError):
            parse_reply(reply(1), 0)
        value = reply()
        value["commands"][1]["player_id"] = 0
        with self.assertRaises(ValueError):
            parse_reply(value, 0)

    def test_rejects_boolean_ids_nonfinite_and_unknown_commands(self):
        for field, bad in (
            ("player_id", True),
            ("target_x", float("nan")),
            ("target_x", 10**400),
            ("target_y", "0"),
            ("command_type", "IDLE"),
        ):
            with self.subTest(field=field):
                value = reply()
                value["commands"][0][field] = bad
                with self.assertRaises(ValueError):
                    parse_reply(value, 0)
        value = reply()
        value["schema_version"] = True
        with self.assertRaises(ValueError):
            parse_reply(value, 0)

    def test_accepts_complete_five_player_reply(self):
        parsed = parse_reply(reply(), 0)
        self.assertEqual([c["player_id"] for c in parsed["commands"]], list(range(5)))
