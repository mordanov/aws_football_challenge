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
    supporting = []
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
            supporting.append(match)
    matches = tuple(sorted(set(supporting)))
    if len(matches) < 2 or profile.settings.press_radius <= 12:
        return None
    evidence_digest = hashlib.sha256(canonical(sorted(unique)).encode()).hexdigest()
    settings = replace(
        profile.settings, press_radius=max(12.0, profile.settings.press_radius - 2.0)
    )
    candidate = replace(
        profile,
        version="auto_"
        + hashlib.sha256(canonical([digest, settings.__dict__]).encode()).hexdigest()[:20],
        settings=settings,
    )
    parse_profile(candidate.to_dict())
    return Candidate(
        candidate,
        evidence_digest,
        matches,
        "repeated observed central exposure: test smaller routine press radius; "
        "no causal failure or guaranteed improvement inferred",
    )
