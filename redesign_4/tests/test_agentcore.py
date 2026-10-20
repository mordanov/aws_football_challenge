import importlib.util
import json
import unittest
from unittest.mock import patch

from test_runtime import payload

from agent import main
from agent.runtime import Runtime


@unittest.skipUnless(
    importlib.util.find_spec("bedrock_agentcore"), "optional deployment SDK absent"
)
class AgentCoreTests(unittest.IsolatedAsyncioTestCase):
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
