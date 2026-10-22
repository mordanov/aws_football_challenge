from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from itertools import pairwise
from pathlib import Path

from .model import Profile, Settings, Snapshot
from .profile import parse_profile, save_profile
from .protocol import integer, restore_snapshot, snapshot_dict, validate_command
from .tactics import distance, point, shot_option

ROLES = ("GK", "DEF", "MID", "FWD1", "FWD2")


def canonical(data: dict) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)


def nonempty(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def timestamp(value: object) -> datetime:
    try:
        result = datetime.fromisoformat(nonempty(value, "played_at"))
    except (TypeError, ValueError) as exc:
        raise ValueError("played_at requires an ISO-8601 timestamp with timezone") from exc
    if result.tzinfo is None:
        raise ValueError("played_at requires a timezone")
    return result.astimezone(timezone.utc)


def checked_manifest(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise TypeError("manifest must be an object")
    result: dict = {key: nonempty(raw.get(key), key) for key in ("match_id", "opponent_id")}
    result["team_id"] = integer(raw.get("team_id"), "team_id", 0, 1)
    if type(raw.get("complete")) is not bool:
        raise ValueError("manifest complete must be boolean")
    result["complete"] = raw["complete"]
    score = raw.get("result")
    if result["complete"]:
        if not isinstance(score, list) or len(score) != 2:
            raise ValueError("complete match requires result [our_goals, opponent_goals]")
        score = [integer(v, "result", 0, 10000) for v in score]
    elif score is not None:
        raise ValueError("partial match must not claim a final result")
    result["result"] = score
    result["played_at"] = timestamp(raw.get("played_at")).isoformat()
    if set(raw) != set(result):
        raise ValueError("manifest has missing or unknown fields")
    return result


def adjacent(a: Snapshot, b: Snapshot) -> bool:
    return b.tick > a.tick and 0 < b.clock - a.clock <= 4.1 and a.score == b.score


def _scouting(observations: list[Snapshot]) -> dict:
    line, width, pressure = [], [], []
    flanks: Counter[str] = Counter()
    outlets: Counter[str] = Counter()
    opponent_losses = []
    for s in observations:
        opps = tuple(p for p in s.opponents if p.id != 0)
        line.append(sum(s.direction * p.x for p in opps) / len(opps))
        width.append(sum(abs(p.y) for p in opps) / len(opps))
        if s.owner and s.owner[0] == s.team:
            pressure.append(sum(distance(point(p), s.ball) < 8 for p in opps))
        if s.owner and s.owner[0] != s.team:
            outlets[str(s.owner[1])] += 1
        for p in opps:
            if s.direction * p.x > 0:
                flanks["negative_y" if p.y < 0 else "positive_y"] += 1
    for a, b in pairwise(observations):
        if adjacent(a, b) and a.owner and b.owner and a.owner[0] != a.team and b.owner[0] == b.team:
            opponent_losses.append(
                {
                    "ticks": [a.tick, b.tick],
                    "player_id": a.owner[1],
                    "location": list(a.ball),
                    "confidence": "inferred",
                }
            )
    return {
        "samples": len(observations),
        "mean_line": round(sum(line) / len(line), 3) if line else None,
        "mean_width": round(sum(width) / len(width), 3) if width else None,
        "mean_pressers": round(sum(pressure) / len(pressure), 3) if pressure else None,
        "defensive_flank_occupancy": dict(flanks),
        "possession_locations": dict(outlets),
        "possible_opponent_losses": opponent_losses,
    }


def analyze(
    manifest: dict, observations: list[Snapshot], decisions: list[dict], errors: list[dict]
) -> dict:
    by_tick = {s.tick: i for i, s in enumerate(observations)}
    outcomes = {
        "confirmed_passes": 0,
        "confirmed_goals": 0,
        "possible_completed_passes": 0,
        "possible_pass_losses": 0,
        "unknown_attempts": 0,
    }
    lessons = []

    def lesson(kind, role, ticks, confidence, evidence, remedy):
        lessons.append(
            {
                "kind": kind,
                "role": role,
                "ticks": ticks,
                "confidence": confidence,
                "evidence": evidence,
                "remedy": remedy,
            }
        )

    for record in decisions:
        current = observations[by_tick[record["snapshot"]["tick"]]]
        index = by_tick[current.tick]
        following = None
        for candidate in observations[index + 1 :]:
            if not adjacent(current, candidate):
                break
            if not candidate.free:
                following = candidate
                break
        cmd, pid = record["command"], record["player_id"]
        role = ROLES[pid]
        if cmd["commandType"] in ("PASS", "GK_DISTRIBUTE", "SHOOT"):
            if current.owner != (current.team, pid):
                lesson(
                    "possession_error",
                    role,
                    [current.tick],
                    "observed",
                    "possession command without confirmed ownership",
                    "repair ownership/adapter before tactical tuning",
                )
                outcomes["unknown_attempts"] += 1
                continue
            if cmd["commandType"] == "SHOOT":
                outcomes["unknown_attempts"] += 1
                if shot_option(current, current.player(pid), Settings()) is None:
                    lesson(
                        "poor_shot",
                        role,
                        [current.tick],
                        "inferred",
                        "shot outside baseline angle/range/lane checks",
                        "reduce shot range; recycle to an open receiver",
                    )
                continue
            if following is None or not adjacent(current, following):
                outcomes["unknown_attempts"] += 1
            elif following.owner and following.owner[0] != current.team:
                outcomes["possible_pass_losses"] += 1
                lesson(
                    "risky_outlet",
                    role,
                    [current.tick, following.tick],
                    "inferred",
                    "pass attempt followed within 4.1 seconds by opponent possession",
                    "increase lane/receiver safety; offer a wider diagonal outlet",
                )
            elif following.owner == (current.team, cmd["parameters"]["target_player_id"]):
                outcomes["possible_completed_passes"] += 1
            else:
                outcomes["unknown_attempts"] += 1
        if record.get("reason") == "hold balanced: no safe supported possession action":
            lesson(
                "no_outlet",
                role,
                [current.tick],
                "observed",
                "carrier found no safe outlet",
                "widen diagonal support and review receiver pressure",
            )

    for s in observations:
        if s.owner is None or s.owner[0] == s.team:
            continue
        goal = (-s.direction * 55, 0)
        if s.direction * s.ball[0] < -20 and abs(s.ball[1]) < 16:
            covering = [
                p
                for p in s.ours
                if p.id != 0 and s.direction * p.x < s.direction * s.ball[0] and abs(p.y) < 14
            ]
            if not covering:
                lesson(
                    "central_exposure",
                    "DEF/MID",
                    [s.tick],
                    "observed",
                    "opponent central possession with no goal-side central outfielder",
                    "retain a covering midfielder when the defender engages",
                )
        for threat in s.opponents:
            if threat.id == 0 or (threat.team, threat.id) == s.owner:
                continue
            if distance(point(threat), goal) < 22 and all(
                distance(point(p), point(threat)) > 7 for p in s.ours if p.id != 0
            ):
                lesson(
                    "uncovered_runner",
                    "DEF/MID",
                    [s.tick],
                    "observed",
                    f"uncovered dangerous opponent {threat.id}",
                    "prioritize goal-danger marking before high pressing",
                )
    for error in errors:
        lesson(
            "runtime_error",
            ROLES[error["player_id"]],
            [error.get("tick")],
            "observed",
            error["error"],
            "repair the recorded integration failure before tuning",
        )

    scout = _scouting(observations)
    if scout["samples"] and scout["mean_line"] < -5:
        lesson(
            "opponent_high_line",
            "MID/FWD",
            [observations[0].tick, observations[-1].tick],
            "inferred",
            f"mean opponent line {scout['mean_line']:.1f} in our attack frame",
            "stretch one forward; favor safe through passes into space",
        )
    return {
        "match_id": manifest["match_id"],
        "opponent_id": manifest["opponent_id"],
        "complete": manifest["complete"],
        "result": manifest["result"],
        "observations": len(observations),
        "decisions": len(decisions),
        "errors": len(errors),
        "outcomes": outcomes,
        "lessons": lessons,
        "scouting": scout,
        "promotion": "proposal_only",
        "evaluation": (
            "insufficient comparable matches to claim improvement; result is not causal proof"
            if manifest["complete"]
            else "insufficient final-result evidence: partial match"
        ),
        "limitations": [
            "snapshot transitions infer, not confirm, action outcomes",
            "no server acknowledgement, shot events, timeout evidence or report API",
            "manifest completeness describes result availability, not full replay coverage",
        ],
    }


class Trainer:
    def __init__(self, database: str | Path):
        self.db = sqlite3.connect(database)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS matches (
                id TEXT PRIMARY KEY, manifest TEXT NOT NULL, profile TEXT NOT NULL,
                report TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS observations (
                match_id TEXT REFERENCES matches(id), tick INTEGER, payload TEXT NOT NULL,
                PRIMARY KEY (match_id, tick)
            );
            CREATE TABLE IF NOT EXISTS decisions (
                match_id TEXT REFERENCES matches(id), tick INTEGER, player INTEGER,
                payload TEXT NOT NULL, PRIMARY KEY (match_id, tick, player)
            );
            CREATE TABLE IF NOT EXISTS errors (
                match_id TEXT REFERENCES matches(id), fingerprint TEXT, payload TEXT NOT NULL,
                PRIMARY KEY (match_id, fingerprint)
            );
            CREATE TABLE IF NOT EXISTS profiles (
                version TEXT PRIMARY KEY, payload TEXT NOT NULL, audit TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS proposals (
                version TEXT REFERENCES profiles(version), setting TEXT, payload TEXT NOT NULL,
                PRIMARY KEY (version, setting)
            );
            CREATE TABLE IF NOT EXISTS promotions (
                id INTEGER PRIMARY KEY, version TEXT REFERENCES profiles(version),
                note TEXT NOT NULL, accepted_at TEXT NOT NULL
            );
        """)

    def close(self) -> None:
        self.db.close()

    def _insert_unique(self, table: str, keys: dict, data: dict) -> None:
        # Table and key names are internal constants, never CLI or file input.
        where = " AND ".join(f"{key}=?" for key in keys)
        values = tuple(keys.values())
        existing = self.db.execute(f"SELECT payload FROM {table} WHERE {where}", values).fetchone()
        encoded = canonical(data)
        if existing:
            if existing[0] != encoded:
                raise ValueError(f"conflicting duplicate in {table}: {keys}")
        else:
            columns = ",".join((*keys, "payload"))
            slots = ",".join("?" for _ in range(len(keys) + 1))
            self.db.execute(f"INSERT INTO {table} ({columns}) VALUES ({slots})", (*values, encoded))

    def ingest(self, manifest: dict, records: list[dict]) -> dict:
        manifest = checked_manifest(manifest)
        mid = manifest["match_id"]
        if not isinstance(records, list) or not records:
            raise ValueError("at least one structured record is required")
        versions = {
            nonempty(r.get("profile_version"), "profile_version")
            for r in records
            if isinstance(r, dict)
        }
        if len(versions) != 1:
            raise ValueError("one pinned profile version per match is required")
        version = next(iter(versions))
        with self.db:
            row = self.db.execute(
                "SELECT manifest, profile FROM matches WHERE id=?", (mid,)
            ).fetchone()
            if row:
                previous = json.loads(row[0])
                same_identity = all(
                    previous[k] == manifest[k]
                    for k in ("match_id", "opponent_id", "team_id", "played_at")
                )
                finalizing = not previous["complete"] and manifest["complete"] and same_identity
                if row[1] != version or (row[0] != canonical(manifest) and not finalizing):
                    raise ValueError(
                        "match manifest/profile conflicts with previously imported match"
                    )
                if finalizing:
                    self.db.execute(
                        "UPDATE matches SET manifest=? WHERE id=?", (canonical(manifest), mid)
                    )
            self.db.execute(
                "INSERT OR IGNORE INTO matches VALUES (?,?,?,?)",
                (mid, canonical(manifest), version, "{}"),
            )
            for record in records:
                if not isinstance(record, dict):
                    raise TypeError("record must be an object")
                if (
                    record.get("match_id") != mid
                    or record.get("opponent_id") != manifest["opponent_id"]
                    or record.get("team_id") != manifest["team_id"]
                ):
                    raise ValueError("record identity disagrees with manifest")
                pid = integer(record.get("player_id"), "player_id", 0, 4)
                if record.get("type") == "error":
                    nonempty(record.get("error"), "error")
                    tick = record.get("tick")
                    if tick is not None:
                        integer(tick, "tick", 0, 10**12)
                    fingerprint = hashlib.sha256(canonical(record).encode()).hexdigest()
                    self._insert_unique(
                        "errors", {"match_id": mid, "fingerprint": fingerprint}, record
                    )
                    continue
                if record.get("type") != "decision":
                    raise ValueError("unknown record type")
                snapshot = restore_snapshot(record.get("snapshot"))
                if snapshot.team != manifest["team_id"]:
                    raise ValueError("snapshot team disagrees with manifest")
                cmd = record["command"]
                validate_command(cmd)
                if cmd["teamId"] != snapshot.team or cmd["playerId"] != pid:
                    raise ValueError("command identity disagrees with record")
                self._insert_unique(
                    "observations",
                    {"match_id": mid, "tick": snapshot.tick},
                    snapshot_dict(snapshot),
                )
                # Latency and collector fields may differ when exporting the same action twice.
                decision = {
                    key: record[key]
                    for key in (
                        "type",
                        "match_id",
                        "opponent_id",
                        "profile_version",
                        "team_id",
                        "player_id",
                        "command",
                        "snapshot",
                        "reason",
                        "alternatives",
                    )
                    if key in record
                }
                decision["snapshot"] = snapshot_dict(snapshot)
                self._insert_unique(
                    "decisions", {"match_id": mid, "tick": snapshot.tick, "player": pid}, decision
                )
            observations = [
                restore_snapshot(json.loads(row[0]))
                for row in self.db.execute(
                    "SELECT payload FROM observations WHERE match_id=? ORDER BY tick", (mid,)
                )
            ]
            for a, b in pairwise(observations):
                if b.clock <= a.clock or any(b.score[i] < a.score[i] for i in (0, 1)):
                    raise ValueError("clock and score must not go backward within a match")
            if manifest["complete"]:
                final = manifest["result"] if manifest["team_id"] == 0 else manifest["result"][::-1]
                if any(s.score[i] > final[i] for s in observations for i in (0, 1)):
                    raise ValueError("observed score exceeds the declared final result")
            decisions = [
                json.loads(row[0])
                for row in self.db.execute(
                    "SELECT payload FROM decisions WHERE match_id=? ORDER BY tick, player", (mid,)
                )
            ]
            errors = [
                json.loads(row[0])
                for row in self.db.execute(
                    "SELECT payload FROM errors WHERE match_id=? ORDER BY fingerprint", (mid,)
                )
            ]
            report = analyze(manifest, observations, decisions, errors)
            self.db.execute("UPDATE matches SET report=? WHERE id=?", (canonical(report), mid))
        return report

    def report(self, match_id: str) -> dict:
        row = self.db.execute("SELECT report FROM matches WHERE id=?", (match_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown match: {match_id}")
        return json.loads(row[0])

    def train(self) -> Profile:
        rows = self.db.execute(
            "SELECT manifest, report, profile FROM matches ORDER BY id"
        ).fetchall()
        if not rows:
            raise ValueError("import at least one match before training")
        matches = [(json.loads(m), json.loads(r), p) for m, r, p in rows]
        latest = max(timestamp(m["played_at"]) for m, _, _ in matches)
        reference = max(datetime.now(timezone.utc), latest).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        defaults = Settings()
        changes = []
        proposals = []
        values = asdict(defaults)
        for kind, setting, delta, remedy in (
            ("risky_outlet", "pass_margin", 0.25, "increase passing safety"),
            ("poor_shot", "shot_range", -1.0, "reduce speculative shooting"),
            ("no_outlet", "support_width", 1.0, "create wider supporting outlets"),
        ):
            supporting = [
                m["match_id"]
                for m, report, _ in matches
                if m["complete"]
                and (reference - timestamp(m["played_at"])).days <= 30
                and len({x["ticks"][0] for x in report["lessons"] if x["kind"] == kind}) >= 3
            ]
            evidence = [
                m["match_id"]
                for m, report, _ in matches
                if any(x["kind"] == kind for x in report["lessons"])
            ]
            if evidence:
                proposals.append(
                    {
                        "setting": setting,
                        "before": values[setting],
                        "proposed": values[setting] + delta,
                        "evidence": evidence,
                        "eligible_evidence": supporting,
                        "reason": remedy,
                        "status": "qualified_candidate"
                        if len(supporting) >= 3
                        else "insufficient_evidence",
                    }
                )
            if len(supporting) >= 3:
                values[setting] += delta
                changes.append(
                    {
                        "setting": setting,
                        "before": asdict(defaults)[setting],
                        "after": values[setting],
                        "evidence": supporting,
                        "reason": remedy,
                        "status": "bounded_candidate_not_proven_improvement",
                    }
                )
        settings = Settings(**values)
        groups = defaultdict(list)
        for manifest, report, _ in matches:
            groups[manifest["opponent_id"]].append((manifest, report))
        scouts = {}
        for opponent, group in sorted(groups.items()):
            count = sum(r["scouting"]["samples"] for _, r in group)
            if count == 0:
                continue
            weighted = []
            for m, r in group:
                age = max(0, (reference - timestamp(m["played_at"])).total_seconds() / 86400)
                recency = math.pow(0.5, age / 30)
                weighted.append((r, r["scouting"]["samples"] * recency))
            mass = sum(w for _, w in weighted)
            weight = mass / (mass + 12)
            line = (
                sum(
                    r["scouting"]["mean_line"] * w
                    for r, w in weighted
                    if r["scouting"]["mean_line"] is not None
                )
                / mass
                if mass
                else 0
            )
            press_mass = sum(w for r, w in weighted if r["scouting"]["mean_pressers"] is not None)
            press = (
                sum((r["scouting"]["mean_pressers"] or 0) * w for r, w in weighted) / press_mass
                if press_mass
                else 0
            )
            tuned = asdict(settings)
            losses = sum(len(r["scouting"]["possible_opponent_losses"]) for _, r in group)
            effective_losses = sum(
                len(r["scouting"]["possible_opponent_losses"]) * w / r["scouting"]["samples"]
                for r, w in weighted
                if r["scouting"]["samples"]
            )
            if count >= 6 and len(group) >= 2:
                if line < -5:
                    tuned["support_width"] = min(22, tuned["support_width"] + 2 * weight)
                if press >= 1.5:
                    tuned["pass_margin"] = min(4.5, tuned["pass_margin"] + 0.5 * weight)
                if effective_losses >= 1.5:
                    loss_weight = effective_losses / (effective_losses + 3)
                    tuned["press_radius"] = min(30, tuned["press_radius"] + 2 * loss_weight)
            scouts[opponent] = {
                "samples": count,
                "matches": len(group),
                "weight": round(weight, 6),
                "mean_line": round(line, 3),
                "mean_pressers": round(press, 3),
                "possible_possession_losses": losses,
                "effective_possession_losses": round(effective_losses, 6),
                "settings": {key: round(value, 6) for key, value in tuned.items()},
            }
        material = {"settings": asdict(settings), "opponents": scouts}
        digest = hashlib.sha256(
            canonical(
                {"material": material, "matches": rows, "as_of": reference.isoformat()}
            ).encode()
        ).hexdigest()[:16]
        profile = parse_profile({"version": f"trained-{digest}", **material})
        comparisons: defaultdict[str, dict[str, int]] = defaultdict(
            lambda: {"matches": 0, "wins": 0, "goal_difference": 0}
        )
        for manifest, _, version in matches:
            if manifest["complete"]:
                key = f"{manifest['opponent_id']} / {version}"
                result = manifest["result"]
                comparisons[key]["matches"] += 1
                comparisons[key]["wins"] += int(result[0] > result[1])
                comparisons[key]["goal_difference"] += result[0] - result[1]
        audit = {
            "as_of": reference.isoformat(),
            "changes": changes,
            "comparison_by_opponent_and_profile": dict(comparisons),
            "warning": "candidate profile; observational results do not establish causality",
        }
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO profiles VALUES (?,?,?)",
                (profile.version, canonical(profile.to_dict()), canonical(audit)),
            )
            for proposal in proposals:
                self.db.execute(
                    "INSERT OR IGNORE INTO proposals VALUES (?,?,?)",
                    (profile.version, proposal["setting"], canonical(proposal)),
                )
        return profile

    def export(self, version: str, path: str | Path) -> None:
        row = self.db.execute("SELECT payload FROM profiles WHERE version=?", (version,)).fetchone()
        if row is None:
            raise KeyError(f"unknown profile version: {version}")
        save_profile(parse_profile(json.loads(row[0])), path)

    def audit(self, version: str) -> dict:
        row = self.db.execute("SELECT audit FROM profiles WHERE version=?", (version,)).fetchone()
        if row is None:
            raise KeyError(f"unknown profile version: {version}")
        audit = json.loads(row[0])
        audit["proposals"] = [
            json.loads(r[0])
            for r in self.db.execute(
                "SELECT payload FROM proposals WHERE version=? ORDER BY setting", (version,)
            )
        ]
        audit["promotions"] = [
            {
                "event_id": r[0],
                "version": version,
                "note": r[1],
                "accepted_at": r[2],
                "status": "operator_approved",
            }
            for r in self.db.execute(
                "SELECT id,note,accepted_at FROM promotions WHERE version=? ORDER BY id", (version,)
            )
        ]
        return audit

    def promote(self, version: str, note: str) -> dict:
        nonempty(note, "promotion note")
        if self.db.execute("SELECT 1 FROM profiles WHERE version=?", (version,)).fetchone() is None:
            raise KeyError(f"unknown profile version: {version}")
        accepted_at = datetime.now(timezone.utc).isoformat()
        with self.db:
            event = self.db.execute(
                "INSERT INTO promotions(version,note,accepted_at) VALUES (?,?,?)",
                (version, note, accepted_at),
            )
        return {
            "event_id": event.lastrowid,
            "version": version,
            "note": note,
            "accepted_at": accepted_at,
            "status": "operator_approved",
        }
