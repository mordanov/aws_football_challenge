import importlib.util
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_runtime import payload

from agent import main
from agent.runtime import Runtime


@unittest.skipUnless(
    importlib.util.find_spec("bedrock_agentcore"), "optional deployment SDK absent"
)
class AgentCoreTests(unittest.IsolatedAsyncioTestCase):
    def test_captured_forward_at_boundary_shoots_instead_of_carrying(self):
        # Tick 7175: reconstructed from normalized telemetry; original ball z was not logged.
        positions = (
            (-6.4, -1.4012938),
            (-6.4, 0.31446734),
            (-0.55346316, -3.5),
            (6.4, -0.22905341),
            (5.125767, 3.6000004),
            (-6.4, -0.45149958),
            (4.8811307, 0.19935137),
            (5.2075233, -0.3268969),
            (6.047864, 1.6006875),
            (5.374957, -0.1920256),
        )
        data = json.loads(payload(owner=(0, 3), pid=3)["prompt"])
        for player, (x, y) in zip(data["gameState"]["players"], positions, strict=True):
            player["position"] = {"x": x, "y": y}
            player["stamina"] = 1.0
        data["gameState"]["ball"]["position"] = {"x": 6.744304, "y": 0.14291038}
        records = []
        runtime = Runtime(stamina_scale=100, competition_adapter=True, emit=records.append)
        self.addCleanup(runtime.close)
        session = "4e7baceb-646a-4338-b30a-dea31ce9a3b5-team0-pos3"
        command = json.loads(runtime.handle(data, session_id=session))[0]
        self.assertEqual(command["commandType"], "SHOOT")
        self.assertTrue(runtime.flush_logs())
        self.assertEqual(records[-1]["snapshot"]["players"][3]["x"], 51.2)
        self.assertEqual(records[-1]["snapshot"]["players"][3]["stamina"], 1.0)

    def test_unrecognized_competition_session_warns_without_guessing_units(self):
        records = []
        runtime = Runtime(stamina_scale=100, competition_adapter=True, emit=records.append)
        self.addCleanup(runtime.close)
        data = json.loads(payload()["prompt"])
        for player in data["gameState"]["players"]:
            player["stamina"] = 1
        runtime.handle(data, session_id="unknown-server-session-format-12345")
        self.assertTrue(runtime.flush_logs())
        self.assertEqual(records[-1]["input_format"], "sample")
        self.assertEqual(records[-1]["snapshot"]["players"][0]["stamina"], 0.01)
        self.assertIn("unrecognized_competition_session", records[-1]["warnings"])

    async def test_live_match_uses_compact_geometry_and_fractional_stamina(self):
        from httpx import ASGITransport, AsyncClient

        match_id = "4e7baceb-646a-4338-b30a-dea31ce9a3b5"
        records = []
        runtime = Runtime(stamina_scale=100, competition_adapter=True, emit=records.append)
        self.addCleanup(runtime.close)
        app = main.create_app(runtime)
        data = json.loads(payload(owner=(0, 3), ball=(39, -5), pid=3)["prompt"])
        state = data["gameState"]
        state["players"][3]["position"] = {"x": 39, "y": -5}
        for player in state["players"]:
            player["position"] = {k: v / 8 for k, v in player["position"].items()}
            player["stamina"] = 0.01 if player["agentId"] == "agentId_2" else 0.9
        state["ball"]["position"] = {"x": 39 / 8, "y": 0.14291, "z": -5 / 8}
        state["ball"]["velocity"] = {"x": 0.25, "y": 4, "z": -0.125}
        async with AsyncClient(transport=ASGITransport(app), base_url="http://local") as client:
            response = await client.post(
                "/invocations",
                json={"prompt": data},
                headers={"X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": f"{match_id}-team0-pos3"},
            )
        event = next(x[6:] for x in response.text.splitlines() if x.startswith("data: "))
        command = json.loads(json.loads(event))[0]
        self.assertEqual(command["commandType"], "SHOOT")
        self.assertTrue(runtime.flush_logs())
        record = records[-1]
        self.assertEqual(record["match_id"], match_id)
        self.assertEqual(record["input_format"], "compact_match")
        self.assertEqual(record["snapshot"]["ball"], [39.0, -5.0])
        self.assertEqual(record["snapshot"]["velocity"], [2.0, -1.0])
        self.assertEqual(record["snapshot"]["players"][2]["stamina"], 0.01)

    async def test_live_movement_is_converted_back_without_changing_fitness_format(self):
        from httpx import ASGITransport, AsyncClient

        records = []
        runtime = Runtime(stamina_scale=100, competition_adapter=True, emit=records.append)
        self.addCleanup(runtime.close)
        app = main.create_app(runtime)
        async with AsyncClient(transport=ASGITransport(app), base_url="http://local") as client:
            for team in (0, 1):
                data = json.loads(payload(owner=None, team=team, pid=0)["prompt"])
                for player in data["gameState"]["players"]:
                    player["position"] = {k: v / 8 for k, v in player["position"].items()}
                data["gameState"]["ball"]["position"] = {"x": 0, "y": 0.14, "z": 2}
                response = await client.post(
                    "/invocations",
                    json={"prompt": data},
                    headers={
                        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": f"4e7baceb-646a-4338-b30a-dea31ce9a3b5-team{team}-pos0"
                    },
                )
                event = next(x[6:] for x in response.text.splitlines() if x.startswith("data: "))
                command = json.loads(json.loads(event))[0]
                self.assertEqual(command["commandType"], "MOVE_TO")
                self.assertEqual(command["parameters"]["target_x"], -6.4 if team == 0 else 6.4)
                self.assertGreater(command["parameters"]["target_y"], 0)
                self.assertLessEqual(command["parameters"]["target_y"], 3.5)
            fitness = json.loads(payload(pid=0)["prompt"])
            for player in fitness["gameState"]["players"]:
                player["stamina"] = 1
            response = await client.post(
                "/invocations",
                json={"prompt": fitness},
                headers={
                    "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": "scrimmage-drill-ede88560-dc5e-418c-8eb4-e06046e19199-pos0"
                },
            )
        event = next(x[6:] for x in response.text.splitlines() if x.startswith("data: "))
        command = json.loads(json.loads(event))[0]
        self.assertLess(command["parameters"]["target_x"], -50)
        self.assertTrue(runtime.flush_logs())
        self.assertEqual(records[-1]["snapshot"]["players"][0]["stamina"], 0.01)
        self.assertIsNone(records[-1]["match_id"])

    def test_deployed_startup_handles_percentage_stamina(self):
        spec = json.loads(
            (Path(__file__).resolve().parents[1] / "deploy/agentcore/agentcore.json").read_text()
        )
        for seat, deployment in enumerate(spec["runtimes"]):
            for stamina in (100, 1, 0):
                with self.subTest(runtime=deployment["name"], stamina=stamina):
                    data = json.loads(payload(owner=(0, 0), ball=(-50.5, 0), pid=seat)["prompt"])
                    del data["myPlayers"]
                    for player in data["gameState"]["players"]:
                        player["stamina"] = stamina
                    records = []

                    def serve(runtime, default_player):
                        def invoke():
                            result = runtime.handle(
                                main.with_default_player({"prompt": data}, default_player)
                            )
                            command = json.loads(result)[0]
                            self.assertEqual(command["playerId"], seat)
                            self.assertNotEqual(command["commandType"], "SET_STANCE")
                            if seat == 0:
                                self.assertEqual(command["commandType"], "GK_DISTRIBUTE")
                            self.assertTrue(runtime.flush_logs())
                            self.assertEqual(records[-1]["type"], "decision")
                            self.assertEqual(
                                records[-1]["snapshot"]["players"][0]["stamina"],
                                stamina / 100,
                            )

                        return SimpleNamespace(run=invoke)

                    env = {item["name"]: item["value"] for item in deployment["envVars"]}
                    with (
                        patch.dict(os.environ, env, clear=True),
                        patch.object(main, "create_app", side_effect=serve),
                        patch("agent.runtime.emit_json", side_effect=records.append),
                    ):
                        main.main()

    async def test_actual_sdk_transport_preserves_command_string_and_identity(self):
        from httpx import ASGITransport, AsyncClient

        logs = []
        runtime = Runtime(emit=logs.append)
        self.addCleanup(runtime.close)
        with patch("socket.socket.connect", side_effect=AssertionError("network forbidden")):
            app = main.create_app(runtime)
            async with app.router.lifespan_context(app):
                async with AsyncClient(
                    transport=ASGITransport(app), base_url="http://local"
                ) as client:
                    health = await client.get("/ping")
                    self.assertEqual(health.status_code, 200)
                    response = await client.post("/invocations", json=payload(team=1, pid=3))
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers["content-type"])
        events = [
            line.removeprefix("data: ")
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        self.assertEqual(len(events), 1)
        commands = json.loads(json.loads(events[0]))
        self.assertEqual(commands[0]["teamId"], 1)
        self.assertEqual(commands[0]["playerId"], 3)
        self.assertTrue(runtime.flush_logs())
        self.assertEqual(logs[0]["command"], commands[0])

    async def test_default_player_only_fills_missing_my_players(self):
        from httpx import ASGITransport, AsyncClient

        runtime = Runtime(emit=lambda _: None)
        self.addCleanup(runtime.close)
        app = main.create_app(runtime, default_player=4)
        bare = json.loads(payload(team=1, pid=3)["prompt"])
        del bare["myPlayers"]
        async with AsyncClient(transport=ASGITransport(app), base_url="http://local") as client:
            missing = await client.post("/invocations", json={"prompt": bare})
            explicit = await client.post("/invocations", json=payload(team=1, pid=3))
        for response, pid in ((missing, 4), (explicit, 3)):
            event = next(x for x in response.text.splitlines() if x.startswith("data: "))
            command = json.loads(json.loads(event.removeprefix("data: ")))[0]
            self.assertEqual((command["teamId"], command["playerId"]), (1, pid))
