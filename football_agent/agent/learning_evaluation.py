"""Offline-only, prespecified paired MatchEngine v2 promotion gate.

Public API:
* ``evaluate_profiles(baseline, candidate)`` runs the *fixed* full schedule.
* ``play(profile, opponent, seed, side)`` returns one raw match result.
* ``evaluate_results(baseline, candidate, rows)`` applies the same gate to evidence.

Reports contain policy_id, accepted, reason, baseline_digest, candidate_digest,
pairs (unmodified raw rows), and statistics. Each row has seed, opponent (pool
name), side (contender's team), baseline and candidate. Match results contain
profile_digest, opponent_digest, seed, side, score ([home, away]), status,
rules_version, duration_ticks, four rejection counters, and all raw rejections
from BOTH teams. Digests are the registry's canonical profile-document digests.
Incomplete runs retain partial rows and return an explicit rejected report.

The independent unit is a seed block, averaging both sides and every opponent.
The one-sided exact binomial sign test treats ties as failures, at alpha=.01,
and additionally requires a positive paired mean, no opponent-average regression,
and no increase in illegal actions or crash windows (for either team). It tests
the null P(block delta > 0) <= .5, assuming independent/exchangeable seed blocks;
it is NOT a confidence bound on expected goal difference. This is a conservative
simulator-only gate, not a platform result or guaranteed real-match improvement.
Fixed seeds and repeated online candidate selection violate fresh-test inference;
the reported p value has no sequential-testing/multiplicity guarantee.

No simulator import, I/O, subprocess, AWS, or LLM call occurs at module import.
Matches use an in-memory store; only the worker needs football_lab installed.
"""

from __future__ import annotations

import math
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from .archetypes import ARCHETYPES
from .coach import choose_preset
from .model import Profile
from .profile import effective_settings, parse_profile
from .profile_registry import profile_document
from .protocol import decode, validate_command
from .tactics import decide

POLICY_ID = "final_agent_v2_paired_seed_sign_12x3x2_alpha01_v1"
SEEDS = tuple(range(1729, 1741))
OPPONENTS = ("baseline", "press_aggressive", "park_the_bus")
ALPHA = 0.01
MAX_SECONDS = 600.0
_COUNTERS = (
    "illegal_actions",
    "agent_crashes",
    "opponent_illegal_actions",
    "opponent_agent_crashes",
)


def _pool(baseline: Profile) -> dict[str, Profile]:
    return {
        "baseline": baseline,
        "press_aggressive": ARCHETYPES["press_aggressive"],
        "park_the_bus": ARCHETYPES["park_the_bus"],
    }


def _counts(rejections: list[dict[str, Any]], side: int) -> dict[str, int]:
    counts = {}
    for team, prefix in ((side, ""), (1 - side, "opponent_")):
        own = [r for r in rejections if r["team_id"] == team]
        counts[prefix + "illegal_actions"] = sum(
            r["rejection_reason"] != "agent_crash" for r in own
        )
        # MatchEngine emits five CRASH rejections for one failed team decision.
        counts[prefix + "agent_crashes"] = len(
            {r["tick"] for r in own if r["rejection_reason"] == "agent_crash"}
        )
    return counts


class _MemoryStore:
    def save(self, replay: Any, path: Path) -> None:
        pass


class _Agent:
    def __init__(self, profile: Profile, opponent_id: str) -> None:
        self.profile = profile
        self.settings = effective_settings(profile, opponent_id)

    def decide(self, observation: dict[str, Any]) -> list[dict[str, Any]]:
        from football_lab.cmd_decoder import decode_command
        from football_lab.obs_encoder import encode_observation

        # Engine observations contain the state fields the encoder consumes.
        # Decode independently for each controlled player, as runtime.handle does.
        commands = []
        side = observation["team_id"]
        for pid in range(5):
            payload = encode_observation(observation, side, [pid], observation["seed"])
            snapshot, player_id = decode(payload)
            if self.profile.coach_policy == "rules":
                snapshot = replace(snapshot, coach=choose_preset(snapshot))
            selected = decide(snapshot, player_id, self.settings, self.profile.style).command
            validate_command(selected)
            commands.append(decode_command(selected, side))
        return commands


def play(profile: Profile, opponent: Profile, seed: int, side: int) -> dict[str, Any]:
    """Play one full 120-s v2 match with no disk writes or hidden fallbacks.

    Agent exceptions are recorded by MatchEngine as raw CRASH rejections.
    Simulator/import failures propagate to evaluate_profiles, which fails closed.
    """
    from football_lab.engine import MatchEngine

    if type(seed) is not int or type(side) is not int or side not in (0, 1):
        raise ValueError("seed must be an integer and side must be 0 or 1")
    profile = parse_profile(profile.to_dict())
    opponent = parse_profile(opponent.to_dict())
    agents = [_Agent(profile, opponent.version), _Agent(opponent, profile.version)]
    if side == 1:
        agents.reverse()
    replay = MatchEngine(seed, agents[0], agents[1], _MemoryStore(), rules_version="v2").run(
        Path("learning-evaluation-in-memory")
    )
    rejections = [dict(r) for r in replay["action_results"] if not r["accepted"]]
    return {
        "profile_digest": profile_document(profile)[0],
        "opponent_digest": profile_document(opponent)[0],
        "seed": seed,
        "side": side,
        "score": list(replay["frames"][-1]["score"]),
        "status": replay["status"],
        "rules_version": replay["rules_version"],
        "duration_ticks": replay["duration_ticks"],
        **_counts(rejections, side),
        "rejections": rejections,
    }


def _validate_match(
    result: dict[str, Any], digest: str, opponent_digest: str, seed: int, side: int
) -> None:
    expected = {
        "profile_digest": digest,
        "opponent_digest": opponent_digest,
        "seed": seed,
        "side": side,
        "status": "complete",
        "rules_version": "v2",
        "duration_ticks": 2400,
    }
    if not isinstance(result, dict) or any(result.get(k) != v for k, v in expected.items()):
        raise ValueError("match identity, rules, or completion mismatch")
    if any(type(result[k]) is not int for k in ("seed", "side", "duration_ticks")):
        raise ValueError("invalid numeric match metadata")
    score = result.get("score")
    if (
        not isinstance(score, list)
        or len(score) != 2
        or any(type(value) is not int or value < 0 for value in score)
    ):
        raise ValueError("invalid raw home/away score")
    rejections = result.get("rejections")
    if not isinstance(rejections, list):
        raise ValueError("missing raw rejections")
    for rejection in rejections:
        if (
            not isinstance(rejection, dict)
            or rejection.get("accepted") is not False
            or type(rejection.get("tick")) is not int
            or not 0 <= rejection["tick"] < 2400
            or type(rejection.get("team_id")) is not int
            or rejection["team_id"] not in (0, 1)
            or type(rejection.get("player_id")) is not int
            or not 0 <= rejection["player_id"] <= 4
            or not isinstance(rejection.get("attempted_type"), str)
            or not isinstance(rejection.get("attempted_params"), dict)
            or not isinstance(rejection.get("rejection_reason"), str)
        ):
            raise ValueError("malformed raw rejection")
    for key, count in _counts(rejections, side).items():
        if type(result.get(key)) is not int or result[key] != count:
            raise ValueError("rejection counters do not match raw evidence")


def evaluate_results(
    baseline: Profile, candidate: Profile, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Validate full fixed-schedule evidence and apply the predeclared verdict.

    This verifies structure and arithmetic, not evidence authenticity. Production
    promotion must use evaluate_profiles, not arbitrary untrusted supplied rows.
    """
    baseline = parse_profile(baseline.to_dict())
    candidate = parse_profile(candidate.to_dict())
    baseline_digest, candidate_digest = (
        profile_document(profile)[0] for profile in (baseline, candidate)
    )
    stats: dict[str, Any] = {
        "scope": "simulator_only_no_real_performance_or_repeated_testing_guarantee",
        "test": "one_sided_exact_sign_ties_as_failures",
        "null": "P(seed_block_delta > 0) <= 0.5; independent/exchangeable seed blocks",
        "alpha": ALPHA,
        "expected_pairs": len(SEEDS) * len(OPPONENTS) * 2,
        "expected_matches_per_contender": len(SEEDS) * len(OPPONENTS) * 2,
    }
    report = {
        "policy_id": POLICY_ID,
        "accepted": False,
        "reason": "invalid_or_incomplete_pairs",
        "baseline_digest": baseline_digest,
        "candidate_digest": candidate_digest,
        "pairs": rows,
        "statistics": stats,
    }
    pool = _pool(baseline)
    try:
        expected = {(seed, name, side) for seed in SEEDS for name in OPPONENTS for side in (0, 1)}
        if not isinstance(rows, list) or len(rows) != len(expected):
            raise ValueError("full fixed schedule required")
        seen = set()
        for row in rows:
            key = (row["seed"], row["opponent"], row["side"])
            if type(key[0]) is not int or type(key[2]) is not int:
                raise ValueError("invalid seed/side")
            if key not in expected or key in seen:
                raise ValueError("unexpected or duplicate pair")
            seen.add(key)
            opponent_digest = profile_document(pool[row["opponent"]])[0]
            for label, digest in (("baseline", baseline_digest), ("candidate", candidate_digest)):
                _validate_match(row[label], digest, opponent_digest, row["seed"], row["side"])
        if seen != expected:
            raise ValueError("missing paired match")
    except (ValueError, TypeError, KeyError) as exc:
        stats["error"] = str(exc)
        return report

    def delta(row: dict[str, Any]) -> int:
        side = row["side"]
        a, b = row["baseline"]["score"], row["candidate"]["score"]
        return (b[side] - b[1 - side]) - (a[side] - a[1 - side])

    block_deltas = [
        sum(delta(row) for row in rows if row["seed"] == seed) / (len(OPPONENTS) * 2)
        for seed in SEEDS
    ]
    wins = sum(value > 0 for value in block_deltas)
    p_value = sum(math.comb(len(SEEDS), k) for k in range(wins, len(SEEDS) + 1)) / 2 ** len(SEEDS)
    totals = {
        label: {key: sum(row[label][key] for row in rows) for key in _COUNTERS}
        for label in ("baseline", "candidate")
    }
    per_opponent = {
        name: {
            "mean_delta": sum(delta(row) for row in rows if row["opponent"] == name)
            / (len(SEEDS) * 2),
            "pairs": len(SEEDS) * 2,
        }
        for name in OPPONENTS
    }
    mean = sum(block_deltas) / len(SEEDS)
    stats.update(
        seed_blocks=len(SEEDS),
        positive_blocks=wins,
        sign_test_p=p_value,
        mean_paired_goal_difference=mean,
        blocks=[{"seed": seed, "mean_delta": value} for seed, value in zip(SEEDS, block_deltas)],
        per_opponent=per_opponent,
        totals=totals,
    )
    baseline_policy, candidate_policy = baseline.to_dict(), candidate.to_dict()
    baseline_policy.pop("version")
    candidate_policy.pop("version")
    reason = "accepted"
    if baseline_policy == candidate_policy:
        reason = "identical_profiles"
    elif any(
        totals["candidate"][key] > totals["baseline"][key]
        for key in ("agent_crashes", "opponent_agent_crashes")
    ):
        reason = "agent_crash_increase"
    elif any(
        totals["candidate"][key] > totals["baseline"][key]
        for key in ("illegal_actions", "opponent_illegal_actions")
    ):
        reason = "illegal_action_increase"
    elif any(values["mean_delta"] < 0 for values in per_opponent.values()):
        reason = "opponent_regression"
    elif mean <= 0:
        reason = "nonpositive_mean_delta"
    elif p_value > ALPHA:
        reason = "insufficient_sign_evidence"
    report.update(accepted=reason == "accepted", reason=reason)
    return report


def evaluate_profiles(baseline: Profile, candidate: Profile) -> dict[str, Any]:
    """Run 144 matches (72 per contender), bounded between matches at 600 s.

    A running match cannot be preempted; elapsed time is checked immediately
    before AND after each match. No partial, overtime, or failed run can pass.
    """
    started = time.monotonic()
    baseline = parse_profile(baseline.to_dict())
    candidate = parse_profile(candidate.to_dict())
    rows: list[dict[str, Any]] = []
    failure = None
    error = None

    def budget() -> None:
        if time.monotonic() - started >= MAX_SECONDS:
            raise TimeoutError("600-second simulator evaluation budget exceeded")

    try:
        for seed in SEEDS:
            for name, opponent in _pool(baseline).items():
                for side in (0, 1):
                    budget()
                    row: dict[str, Any] = {"seed": seed, "opponent": name, "side": side}
                    rows.append(row)
                    for label, profile in (("baseline", baseline), ("candidate", candidate)):
                        budget()
                        row[label] = play(profile, opponent, seed, side)
                        budget()
    except TimeoutError as exc:
        failure, error = "runtime_budget_exceeded", str(exc)
    except Exception as exc:
        failure, error = "simulator_error", f"{type(exc).__name__}: {exc}"
    report = evaluate_results(baseline, candidate, rows)
    report["statistics"]["elapsed_seconds"] = time.monotonic() - started
    if failure is not None:
        report.update(accepted=False, reason=failure)
        report["statistics"]["error"] = error
    return report
