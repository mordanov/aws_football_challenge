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
