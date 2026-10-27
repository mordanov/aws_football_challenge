"""Deterministic, bounded candidate policy over provisional team evidence."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, replace

from .model import Profile, Snapshot
from .profile import parse_profile
from .profile_registry import CODE_GENERATION, canonical, profile_document
from .protocol import restore_snapshot


@dataclass(frozen=True)
class Candidate:
    profile: Profile
    evidence_digest: str
    supporting_matches: tuple[str, ...]
    reason: str


def propose(profile: Profile, documents: list[dict]) -> Candidate | None:
    """One conservative hypothesis, not causal attribution or a claimed remedy."""
    parse_profile(profile.to_dict())
    digest, _ = profile_document(profile)
    if len(documents) > 3000:
        raise ValueError("candidate evidence exceeds 3000 observations")
    groups: dict[tuple[str, int], list[dict]] = defaultdict(list)
    unique: dict[str, dict] = {}
    for doc in documents:
        content = doc["content"]
        identifier = doc["observation_id"]
        if hashlib.sha256(canonical(content).encode()).hexdigest() != identifier:
            raise ValueError("candidate evidence digest mismatch")
        if identifier in unique and unique[identifier]["content"] != content:
            raise ValueError("conflicting observation identity")
        unique[identifier] = doc
    for doc in unique.values():
        content = doc["content"]
        groups[(content["match_id"], content["team_side"])].append(content)
    press_supporting: list[str] = []
    margin_supporting: list[str] = []
    width_supporting: list[str] = []
    shot_supporting: list[str] = []
    for (match, side), records in sorted(groups.items()):
        if any(
            r["kind"] != "decision"
            or r["profile_version"] != profile.version
            or r["profile_digest"] != digest
            or r["code_generation"] != CODE_GENERATION
            or r["final_result"] is not None
            or r["evidence_status"] != "provisional"
            for r in records
        ):
            continue
        contracts = {
            canonical(
                [r["profile_digest"], r["code_generation"], r["input_format"], r["input_contract"]]
            )
            for r in records
        }
        if len(contracts) != 1:
            continue
        frames: dict[float, Snapshot] = {}
        conflict = False
        for r in records:
            s = restore_snapshot(r["snapshot"])
            if s.team != side:
                raise ValueError("candidate snapshot team conflict")
            if s.clock in frames and frames[s.clock] != s:
                # Different ticks at one frozen clock are not necessarily a conflict.
                prior = frames[s.clock]
                if replace(prior, tick=s.tick) != s:
                    conflict = True
            frames.setdefault(s.clock, s)
        if conflict or len(frames) < 5:
            continue
        # Evidence: central exposure — opponent has ball deep in our half with no cover
        exposed = [
            s
            for s in frames.values()
            if s.owner is not None
            and s.owner[0] != side
            and s.direction * s.ball[0] < -20
            and abs(s.ball[1]) < 16
            and not any(
                p.id != 0 and s.direction * p.x < s.direction * s.ball[0] and abs(p.y) < 14
                for p in s.ours
            )
        ]
        if len(exposed) >= 3 and len(exposed) / len(frames) >= 0.10:
            press_supporting.append(match)
        # Evidence: midfield dispossession with nearby support — opponent wins ball in midfield
        # while ≥2 of our non-GK are within 10m, suggesting pass_margin blocked viable lanes
        mid_loss = [
            s
            for s in frames.values()
            if s.owner is not None
            and s.owner[0] != side
            and abs(s.direction * s.ball[0]) < 15
            and sum(
                1
                for p in s.ours
                if p.id != 0
                and (p.x - s.ball[0]) ** 2 + (p.y - s.ball[1]) ** 2 < 100
            ) >= 2
        ]
        if len(mid_loss) >= 3 and len(mid_loss) / len(frames) >= 0.10:
            margin_supporting.append(match)
        # Evidence: narrow attacking shape — all support players within 40% of support_width
        width_threshold = profile.settings.support_width
        narrow: list[Snapshot] = []
        for s in frames.values():
            if s.owner is None or s.owner[0] != side or s.direction * s.ball[0] <= 5:
                continue
            carrier_id = s.owner[1]
            supports = [p for p in s.ours if p.id != 0 and p.id != carrier_id]
            if len(supports) >= 2 and all(abs(p.y) < width_threshold * 0.4 for p in supports):
                narrow.append(s)
        if len(narrow) >= 3 and len(narrow) / len(frames) >= 0.10:
            width_supporting.append(match)
        # Evidence: clear attacking position near shot range with no opponent blocking lane
        shot_threshold = profile.settings.shot_range
        at_range = [
            s
            for s in frames.values()
            if s.owner is not None
            and s.owner[0] == side
            and s.direction * s.ball[0] > 55 - shot_threshold - 5
            and not any(
                p.team != side
                and p.id != 0
                and s.direction * p.x > s.direction * s.ball[0]
                and abs(p.y) < 10
                for p in s.players
            )
        ]
        if len(at_range) >= 3 and len(at_range) / len(frames) >= 0.10:
            shot_supporting.append(match)
    evidence_digest = hashlib.sha256(canonical(sorted(unique)).encode()).hexdigest()
    # Select the first qualifying candidate: press_radius (defensive) first, then the others
    s = profile.settings
    _proposals = [
        (press_supporting, "press_radius", max(12.0, s.press_radius - 2.0), s.press_radius > 12,
         "repeated observed central exposure: test smaller routine press radius; "
         "no causal failure or guaranteed improvement inferred"),
        (margin_supporting, "pass_margin", max(1.5, s.pass_margin - 0.5), s.pass_margin > 1.5,
         "repeated midfield dispossession with nearby support: test lower pass margin; "
         "no causal failure or guaranteed improvement inferred"),
        (width_supporting, "support_width", min(22.0, s.support_width + 2.0), s.support_width < 22,
         "repeated narrow attacking shape: test wider support positions; "
         "no causal failure or guaranteed improvement inferred"),
        (shot_supporting, "shot_range", min(34.0, s.shot_range + 2.0), s.shot_range < 34,
         "repeated clear attacking positions near shot range: test extended shot range; "
         "no causal failure or guaranteed improvement inferred"),
    ]
    for supporting, param, new_value, has_room, reason in _proposals:
        matches = tuple(sorted(set(supporting)))
        if len(matches) < 2 or not has_room:
            continue
        settings = replace(profile.settings, **{param: new_value})
        candidate = replace(
            profile,
            version="auto_"
            + hashlib.sha256(canonical([digest, settings.__dict__]).encode()).hexdigest()[:20],
            settings=settings,
        )
        parse_profile(candidate.to_dict())
        return Candidate(candidate, evidence_digest, matches, reason)
    return None
