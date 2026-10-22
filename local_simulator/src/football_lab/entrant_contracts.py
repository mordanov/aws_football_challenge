"""M3 type contracts for entrants, fixtures, and campaign manifests."""
from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict


class BudgetSpec(TypedDict):
    startup_timeout_s: float
    request_timeout_s: float
    output_size_limit: int  # bytes


class EntrantSpec(TypedDict):
    id: str                    # e.g. "initial_idea_v1"
    source_name: str           # "initial_idea"
    topology: Literal["per_player", "per_team"]
    shim: str                  # path to shim.py relative to local_simulator root
    entrant_hash: str          # sha256 hex of sorted(shim + agent/ files)
    rules_version: Literal["v1"]
    track: Literal["patched", "original"]
    profile: NotRequired[str]  # absolute profile path for a variant (AFC_PROFILE)
    coach: NotRequired[str | None]  # key into football_lab.coaches.COACHES


class FixtureSpec(TypedDict):
    fixture_id: str            # "{team0}__vs__{team1}__b{block:04d}__l{leg}"
    team0_entrant: str         # EntrantSpec.id
    team1_entrant: str
    seed_block: int
    leg: int                   # 0 or 1
    seed: int                  # fixture_seed(pool_start, block, leg)


class FixtureResult(TypedDict):
    fixture_id: str
    status: Literal["complete", "failed"]
    score: list[int]           # [team0_goals, team1_goals]
    failure_reason: str | None
    replay_path: str | None
    duration_ms: float
    team0_rejections: int
    team1_rejections: int
    discipline: NotRequired[dict[str, list[int]]]  # kind -> [team0, team1]


class EntrantStanding(TypedDict):
    entrant_id: str
    games: int
    wins: int
    draws: int
    losses: int
    points: int
    goals_for: int
    goals_against: int
    goal_diff: int


class CampaignManifest(TypedDict):
    campaign_id: str           # ISO timestamp + random hex
    track: Literal["patched", "original", "development"]
    entrants: list[EntrantSpec]
    seed_pool_start: int
    budget: BudgetSpec
    rules_version: Literal["v1", "v2"]
    fixtures: list[FixtureSpec]
    results: list[FixtureResult]
    locked: bool
    rules_params: NotRequired[dict[str, Any]]
    challengers: NotRequired[list[str]]  # cross campaigns only
