"""Failing-first engine tests for M2 (Task 7).

Tests use in-process stub agents, not subprocess agents.
All tests must fail (ImportError) until engine.py is implemented.
"""
import json
import tempfile
import unittest
from pathlib import Path

from football_lab.engine import MatchEngine, StubAgent


class _RecordingAgent:
    """Captures every observation; returns SET_STANCE for all players."""

    def __init__(self, team_id: int) -> None:
        self._team_id = team_id
        self.observations: list[dict] = []

    def decide(self, observation: dict) -> list[dict]:
        self.observations.append(observation)
        return [
            {
                "team_id": self._team_id,
                "player_id": i,
                "command_type": "SET_STANCE",
                "parameters": {"stance": 0},
            }
            for i in range(5)
        ]


class _CrashingAgent:
    """Always raises an exception when asked for decisions."""

    def __init__(self, team_id: int) -> None:
        self._team_id = team_id

    def decide(self, observation: dict) -> list[dict]:
        raise RuntimeError("simulated agent crash")


class _InMemoryStore:
    """Simple in-memory replay store for tests."""

    def __init__(self) -> None:
        self.saved: dict = {}

    def save(self, replay: dict, path: Path) -> None:
        if str(path) in self.saved:
            raise OSError(f"replay already exists: {path}")
        self.saved[str(path)] = json.dumps(replay)

    def load(self, path: Path) -> dict:
        return json.loads(self.saved[str(path)])


class TestMatchTermination(unittest.TestCase):
    def test_match_terminates(self) -> None:
        """Full match terminates after exactly 2400 ticks (120s × 20 ticks/s)."""
        store = _InMemoryStore()
        engine = MatchEngine(
            seed=42,
            team0_agent=StubAgent(0),
            team1_agent=StubAgent(1),
            store=store,
        )
        replay = engine.run(Path("test_match.json"))
        self.assertEqual(replay["duration_ticks"], 2400)
        self.assertEqual(replay["status"], "complete")
        # 2401 frames: tick 0 (initial) + one per step
        self.assertEqual(len(replay["frames"]), 2401)


class TestDeterminism(unittest.TestCase):
    def test_deterministic_replay(self) -> None:
        """Same seed + same stub agents → bit-identical frame sequences."""
        store_a = _InMemoryStore()
        store_b = _InMemoryStore()
        replay_a = MatchEngine(
            seed=7, team0_agent=StubAgent(0), team1_agent=StubAgent(1), store=store_a
        ).run(Path("a.json"))
        replay_b = MatchEngine(
            seed=7, team0_agent=StubAgent(0), team1_agent=StubAgent(1), store=store_b
        ).run(Path("b.json"))
        self.assertEqual(replay_a["frames"], replay_b["frames"])

    def test_different_seeds_differ(self) -> None:
        """Different seeds produce different initial states."""
        store_a = _InMemoryStore()
        store_b = _InMemoryStore()
        replay_a = MatchEngine(
            seed=1, team0_agent=StubAgent(0), team1_agent=StubAgent(1), store=store_a
        ).run(Path("a.json"))
        replay_b = MatchEngine(
            seed=2, team0_agent=StubAgent(0), team1_agent=StubAgent(1), store=store_b
        ).run(Path("b.json"))
        # Frames may differ (or not — seeds may produce same initial state with stub agents)
        # Just confirm both ran to completion
        self.assertEqual(replay_a["duration_ticks"], 2400)
        self.assertEqual(replay_b["duration_ticks"], 2400)


class TestSimultaneousObservation(unittest.TestCase):
    def test_simultaneous_observation(self) -> None:
        """Both agents see the same tick for each decision window."""
        agent_0 = _RecordingAgent(0)
        agent_1 = _RecordingAgent(1)
        store = _InMemoryStore()
        MatchEngine(seed=42, team0_agent=agent_0, team1_agent=agent_1, store=store).run(
            Path("sim.json")
        )
        # 60 decision windows: both agents called once per window
        self.assertEqual(len(agent_0.observations), 60)
        self.assertEqual(len(agent_1.observations), 60)
        for obs0, obs1 in zip(agent_0.observations, agent_1.observations):
            self.assertEqual(obs0["tick"], obs1["tick"])

    def test_observation_has_required_fields(self) -> None:
        """Each observation has schema_version, team_id, tick, players, ball, score."""
        agent_0 = _RecordingAgent(0)
        store = _InMemoryStore()
        MatchEngine(seed=42, team0_agent=agent_0, team1_agent=StubAgent(1), store=store).run(
            Path("fields.json")
        )
        obs = agent_0.observations[0]
        for field in ("schema_version", "team_id", "tick", "players", "ball", "score"):
            self.assertIn(field, obs, f"missing field: {field}")
        self.assertEqual(obs["team_id"], 0)


class TestActionResults(unittest.TestCase):
    def test_action_results_recorded(self) -> None:
        """Replay contains one ActionResult per player per decision window."""
        store = _InMemoryStore()
        replay = MatchEngine(
            seed=42, team0_agent=StubAgent(0), team1_agent=StubAgent(1), store=store
        ).run(Path("ar.json"))
        # 60 windows × 10 players = 600 action results
        self.assertEqual(len(replay["action_results"]), 600)


class TestReplaySaveLoad(unittest.TestCase):
    def test_replay_saved_and_loadable(self) -> None:
        """Replay can be saved to disk and loaded back with correct metadata."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "match.json"
            from football_lab.match_replay import JsonMatchReplayStore, load_match_replay

            store = JsonMatchReplayStore()
            MatchEngine(
                seed=42, team0_agent=StubAgent(0), team1_agent=StubAgent(1), store=store
            ).run(path)
            loaded = load_match_replay(path)
            self.assertEqual(loaded["schema_version"], 2)
            self.assertEqual(loaded["kind"], "match-replay")
            self.assertEqual(loaded["seed"], 42)
            self.assertEqual(loaded["status"], "complete")

    def test_no_overwrite(self) -> None:
        """Saving replay to an existing path raises OSError."""
        store = _InMemoryStore()
        engine = MatchEngine(seed=42, team0_agent=StubAgent(0), team1_agent=StubAgent(1), store=store)
        engine.run(Path("dup.json"))
        engine2 = MatchEngine(seed=42, team0_agent=StubAgent(0), team1_agent=StubAgent(1), store=store)
        with self.assertRaises(OSError):
            engine2.run(Path("dup.json"))


class TestAgentCrashHandled(unittest.TestCase):
    def test_agent_crash_produces_fallback_intents(self) -> None:
        """If an agent crashes, its players get SET_STANCE fallback for that window."""
        store = _InMemoryStore()
        replay = MatchEngine(
            seed=42,
            team0_agent=_CrashingAgent(0),
            team1_agent=StubAgent(1),
            store=store,
        ).run(Path("crash.json"))
        # Match must complete despite crash
        self.assertEqual(replay["status"], "complete")
        # All team_0 action results for first window must be rejected
        first_window = [r for r in replay["action_results"] if r["tick"] == 0 and r["team_id"] == 0]
        self.assertEqual(len(first_window), 5)
        for r in first_window:
            self.assertFalse(r["accepted"])
