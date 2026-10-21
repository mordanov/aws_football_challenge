import subprocess
import sys
import unittest

from football_lab.process_agent import ProcessAgent


class ProcessAgentTests(unittest.TestCase):
    def observation(self):
        return {
            "schema_version": 1,
            "seed": 7,
            "team_id": 1,
            "frame": {"tick": 0, "players": [], "ball": {"x": 0.0, "y": 0.0}},
        }

    def test_real_stub_process_routes_all_five_commands(self):
        agent = ProcessAgent((sys.executable, "-m", "football_lab.stub_agent"))
        reply = agent.decide(self.observation())
        self.assertEqual([c["team_id"] for c in reply["commands"]], [1] * 5)

    def test_rejects_malformed_reply(self):
        agent = ProcessAgent((sys.executable, "-c", "print('not-json')"))
        with self.assertRaises(ValueError):
            agent.decide(self.observation())

    def test_surfaces_nonzero_exit(self):
        agent = ProcessAgent((sys.executable, "-c", "raise SystemExit(3)"))
        with self.assertRaisesRegex(RuntimeError, "exited 3"):
            agent.decide(self.observation())

    def test_watchdog_stops_nonresponsive_stub(self):
        agent = ProcessAgent(
            (sys.executable, "-c", "import time; time.sleep(5)"), timeout=0.1
        )
        with self.assertRaises(subprocess.TimeoutExpired):
            agent.decide(self.observation())
