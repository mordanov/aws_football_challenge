"""Observed opponent behavior is not a confirmed outcome or automatic profile change."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from agent.advisor.offline.contracts import ScoutingContext
from agent.advisor.offline.memory import AgentCoreMemory
from agent.model import Profile
from agent.protocol import snapshot_dict
from tests.offline_support import make_config
from tests.test_offline_memory import FakeMemoryClient
from tests.test_style_tactics import snap

NOW = datetime.now(timezone.utc)
PLAYED = NOW - timedelta(days=1)
EXPIRY = NOW + timedelta(days=5)
PROFILE = Profile(version="scouting-test")


def manifest(team=0, match="match-a"):
    return {
        "match_id": match,
        "opponent_id": "confirmed-opponent",
        "team_id": team,
        "played_at": PLAYED.isoformat(),
        "complete": True,
        "result": [1, 1],
    }


def records(team=0, match="match-a"):
    rows = []
    for tick, clock, advanced in ((1, 5, True), (2, 5, True), (3, 6, False), (4, 7, True)):
        s = snap(
            team=team,
            owner=(1, 1),
            ball=(-20, 0),
            theirs=[(-10, 0), (-20, 0), (-15, 10), (-10, -10), (-5, 10)]
            if advanced
            else [(51, 0), (10, 0), (15, 10), (10, -10), (5, 10)],
        )
        s = replace(s, tick=tick, clock=clock)
        for pid in range(5):
            rows.append(
                {
                    "type": "decision",
                    "match_id": match,
                    "opponent_id": None,
                    "profile_version": PROFILE.version,
                    "team_id": team,
                    "player_id": pid,
                    "input_format": "compact_match",
                    "snapshot": snapshot_dict(s),
                }
            )
    return rows


def prepare(rows=None, team=0, match="match-a"):
    from agent.advisor.offline.scouting import prepare_scouting

    return prepare_scouting(
        manifest(team, match),
        records(team, match) if rows is None else rows,
        team_id="aleksalotl",
        profile=PROFILE,
        expires_at=EXPIRY,
    )


@pytest.mark.parametrize("team", (0, 1))
def test_observations_are_mirrored_deduplicated_and_bound_to_sources(team):
    packet, note = prepare(team=team)
    metrics = [json.loads(c.text) for c in packet.claims]
    assert {m["metric"] for m in metrics} == {"keeper_advanced", "all_players_attacking"}
    assert all((m["positive"], m["samples"]) == (2, 3) for m in metrics)
    assert all(m["positive_ticks"] == [1, 4] for m in metrics)
    assert note.packet_digest == packet.digest
    assert all(sid in packet.source_digests for c in note.claims for sid in c.source_ids)
    client = FakeMemoryClient()
    memory = AgentCoreMemory(client, make_config())
    memory.publish(note, packet)
    assert memory.retrieve(packet).notes == (note,)
    assert len(client.created[0]["payload"][0]["conversational"]["content"]["text"]) < 100_000


@pytest.mark.parametrize(
    "mutation",
    (
        "foreign_match",
        "foreign_team",
        "profile",
        "opponent",
        "conflict",
        "invalid_geometry",
        "unknown_format",
    ),
)
def test_invalid_or_ambiguous_telemetry_fails_instead_of_producing_scouting(mutation):
    rows = records()
    if mutation == "foreign_match":
        rows[0]["match_id"] = "foreign"
    elif mutation == "foreign_team":
        rows[0]["team_id"] = 1
    elif mutation == "profile":
        rows[0]["profile_version"] = "another-profile"
    elif mutation == "opponent":
        rows[0]["opponent_id"] = "foreign"
    elif mutation == "conflict":
        rows[1]["snapshot"]["ball"][0] += 1
    elif mutation == "invalid_geometry":
        rows[0]["snapshot"]["players"][0]["x"] = float("nan")
    else:
        rows[0]["input_format"] = "unrecognized"
    with pytest.raises(ValueError):
        prepare(rows)


def test_no_decisions_or_partial_result_cannot_be_published_as_complete_scouting():
    from agent.advisor.offline.scouting import prepare_scouting

    with pytest.raises(ValueError, match="decision"):
        prepare([])
    m = manifest()
    m.update(complete=False, result=None)
    with pytest.raises(ValueError, match="complete"):
        prepare_scouting(m, records(), team_id="aleksalotl", profile=PROFILE, expires_at=EXPIRY)


def test_brief_retrieves_past_notes_and_preserves_sources_without_applying_profile():
    from agent.advisor.offline.scouting import build_brief

    client = FakeMemoryClient()
    memory = AgentCoreMemory(client, make_config())
    for name in ("match-a", "match-b"):
        packet, note = prepare(match=name)
        memory.publish(note, packet)
        memory.publish(note, packet)
    future_packet, future_note = prepare(match="future-match")
    future_packet = future_packet.model_copy(update={"played_at": NOW + timedelta(hours=1)})
    future_note = future_note.model_copy(
        update={
            "played_at": future_packet.played_at,
            "packet_digest": future_packet.digest,
        }
    )
    memory.publish(future_note, future_packet)
    context = memory.retrieve_before("aleksalotl", "confirmed-opponent", NOW)
    assert len(context.notes) == 2
    assert "not_before_cutoff" in context.excluded
    brief, evidence = build_brief(
        context,
        team_id="aleksalotl",
        opponent_id="confirmed-opponent",
        match_id="next-match",
        profile=PROFILE,
        kickoff=NOW,
    )
    assert brief["status"] == "review_required"
    assert brief["distinct_matches"] == 2
    assert all(item["matches_observed"] == 2 for item in brief["hypotheses"])
    assert evidence.match_id == "next-match"
    assert evidence.complete is False and evidence.result is None
    assert len(memory.retrieve(evidence).notes) == 2
    assert all(c.kind == "hypothesis" for c in evidence.claims)
    assert all(sid in evidence.source_digests for c in evidence.claims for sid in c.source_ids)
    assert PROFILE.to_dict() == Profile(version="scouting-test").to_dict()


def test_single_match_and_duplicate_notes_are_not_repeated_opponent_evidence():
    from agent.advisor.offline.scouting import build_brief

    _, note = prepare()
    brief, evidence = build_brief(
        ScoutingContext(notes=(note, note), extracted_text=(), excluded=()),
        team_id="aleksalotl",
        opponent_id="confirmed-opponent",
        match_id="next-match",
        profile=PROFILE,
        kickoff=NOW,
    )
    assert brief["distinct_matches"] == 1
    assert brief["status"] == "insufficient_evidence"
    assert all(item["positive"] == 2 for item in brief["hypotheses"])
    assert not evidence.claims


def test_advisor_retrieval_does_not_reintroduce_upcoming_match_or_expiry_before_kickoff():
    from agent.advisor.offline.scouting import build_brief

    client = FakeMemoryClient()
    memory = AgentCoreMemory(client, make_config())
    packet, note = prepare(match="next-match")
    memory.publish(note, packet)
    packet, note = prepare(match="expires-soon")
    packet = packet.model_copy(update={"expires_at": NOW + timedelta(hours=1)})
    note = note.model_copy(
        update={
            "expires_at": packet.expires_at,
            "packet_digest": packet.digest,
        }
    )
    memory.publish(note, packet)
    _, query = build_brief(
        ScoutingContext(notes=(), extracted_text=(), excluded=()),
        team_id="aleksalotl",
        opponent_id="confirmed-opponent",
        match_id="next-match",
        profile=PROFILE,
        kickoff=NOW + timedelta(hours=2),
    )
    assert memory.retrieve(query).notes == ()


def test_unknown_possession_cannot_establish_a_full_team_attack():
    rows = records()
    for row in rows:
        row["snapshot"]["owner"] = None
        row["snapshot"]["free"] = False
    packet, _ = prepare(rows)
    assert [json.loads(c.text)["metric"] for c in packet.claims] == ["keeper_advanced"]


def test_future_or_already_expired_matches_are_not_new_scouting():
    from agent.advisor.offline.scouting import prepare_scouting

    m = manifest()
    m["played_at"] = (NOW + timedelta(hours=1)).isoformat()
    with pytest.raises(ValueError, match="future"):
        prepare_scouting(m, records(), team_id="aleksalotl", profile=PROFILE, expires_at=EXPIRY)
    with pytest.raises(ValueError, match="expired"):
        prepare_scouting(
            manifest(),
            records(),
            team_id="aleksalotl",
            profile=PROFILE,
            expires_at=NOW - timedelta(seconds=1),
        )


def test_brief_excludes_other_identity_same_match_expired_future_and_conflicting_notes():
    from agent.advisor.offline.scouting import build_brief

    _, note = prepare()
    variants = [
        note.model_copy(update={"team_id": "other"}),
        note.model_copy(update={"opponent_id": "other"}),
        note.model_copy(update={"match_id": "next-match"}),
        note.model_copy(update={"expires_at": NOW - timedelta(seconds=1)}),
        note.model_copy(update={"played_at": NOW + timedelta(seconds=1)}),
    ]
    context = ScoutingContext(
        notes=tuple(variants), extracted_text=("untrusted text",), excluded=()
    )
    brief, evidence = build_brief(
        context,
        team_id="aleksalotl",
        opponent_id="confirmed-opponent",
        match_id="next-match",
        profile=PROFILE,
        kickoff=NOW,
    )
    assert brief["status"] == "insufficient_evidence"
    assert not evidence.claims
    assert brief["excluded"]
    changed = copy.deepcopy(note.model_dump())
    changed["claims"][0]["text"] = "{}"
    from agent.advisor.offline.contracts import ScoutingNote

    context = ScoutingContext(
        notes=(note, ScoutingNote.model_validate(changed)),
        extracted_text=(),
        excluded=(),
    )
    with pytest.raises(ValueError, match="conflicting"):
        build_brief(
            context,
            team_id="aleksalotl",
            opponent_id="confirmed-opponent",
            match_id="next-match",
            profile=PROFILE,
            kickoff=NOW,
        )


def test_cli_round_trip_generates_publishable_notes_and_advisor_evidence(tmp_path):
    from agent.advisor.offline.__main__ import Services, main
    from agent.advisor.offline.contracts import EvidencePacket

    paths = {
        key: tmp_path / f"{key}.json"
        for key in (
            "manifest",
            "records",
            "profile",
            "packet",
            "note",
            "config",
            "brief",
            "prematch",
        )
    }
    paths["manifest"].write_text(json.dumps(manifest()))
    paths["records"].write_text("\n".join(json.dumps(r) for r in records()))
    paths["profile"].write_text(json.dumps(PROFILE.to_dict()))
    paths["config"].write_text(make_config().model_dump_json())
    assert (
        main(
            [
                "scout",
                "--manifest",
                str(paths["manifest"]),
                "--records",
                str(paths["records"]),
                "--profile",
                str(paths["profile"]),
                "--team",
                "aleksalotl",
                "--expires-at",
                EXPIRY.isoformat(),
                "--output",
                str(paths["packet"]),
                "--note-output",
                str(paths["note"]),
            ]
        )
        == 0
    )
    client = FakeMemoryClient()
    memory = AgentCoreMemory(client, make_config())
    assert (
        main(
            [
                "publish",
                "--config",
                str(paths["config"]),
                "--evidence",
                str(paths["packet"]),
                "--note",
                str(paths["note"]),
            ],
            services=Services(scouts=memory),
        )
        == 0
    )
    # Second independent match supplies repeated evidence, not five-player duplicate votes.
    packet, note = prepare(match="match-b")
    memory.publish(note, packet)
    original_profile = paths["profile"].read_bytes()
    assert (
        main(
            [
                "prematch",
                "--config",
                str(paths["config"]),
                "--team",
                "aleksalotl",
                "--opponent",
                "confirmed-opponent",
                "--match-id",
                "next-match",
                "--kickoff",
                NOW.isoformat(),
                "--profile",
                str(paths["profile"]),
                "--output",
                str(paths["brief"]),
                "--evidence-output",
                str(paths["prematch"]),
            ],
            services=Services(scouts=memory),
        )
        == 0
    )
    assert json.loads(paths["brief"].read_text())["status"] == "review_required"
    query = EvidencePacket.model_validate_json(paths["prematch"].read_text())
    assert len(query.claims) == 2
    assert len(memory.retrieve(query).notes) == 2
    assert paths["profile"].read_bytes() == original_profile
    pytest.importorskip("langchain_core")
    from agent.advisor.offline.bedrock import GroundedAdvisor
    from tests.offline_support import FakeBedrockClient, FakeEvaluator

    client = FakeBedrockClient(
        response_json=json.dumps(
            {
                "settings": PROFILE.to_dict()["settings"],
                "style": PROFILE.to_dict()["style"],
                "rationale": "Evaluate the historical counterattack hypothesis against the unchanged baseline.",
                "evidence_ids": [query.claims[0].id],
                "uncertainties": ["Sample fractions do not establish causal benefit."],
                "counter_tests": ["Replay capture and compare paired full matches."],
            }
        )
    )
    config = make_config().model_copy(update={"max_input_tokens": 32000})
    paths["config"].write_text(config.model_dump_json())
    run_path = tmp_path / "proposal.json"
    assert (
        main(
            [
                "run",
                "--config",
                str(paths["config"]),
                "--evidence",
                str(paths["prematch"]),
                "--profile",
                str(paths["profile"]),
                "--output",
                str(run_path),
            ],
            services=Services(
                scouts=memory,
                advisor=GroundedAdvisor(client, config, {"baseline": PROFILE}),
                evaluator=FakeEvaluator(),
            ),
        )
        == 0
    )
    assert json.loads(run_path.read_text())["status"] == "proposal"
    assert paths["profile"].read_bytes() == original_profile


def test_cli_malformed_records_produce_error_without_artifacts(tmp_path, capsys):
    from agent.advisor.offline.__main__ import main

    for name, value in (
        ("manifest", manifest()),
        ("profile", PROFILE.to_dict()),
        ("records", {"events": []}),
    ):
        (tmp_path / f"{name}.json").write_text(json.dumps(value))
    output = tmp_path / "packet.json"
    assert (
        main(
            [
                "scout",
                "--manifest",
                str(tmp_path / "manifest.json"),
                "--records",
                str(tmp_path / "records.json"),
                "--profile",
                str(tmp_path / "profile.json"),
                "--team",
                "aleksalotl",
                "--expires-at",
                EXPIRY.isoformat(),
                "--output",
                str(output),
                "--note-output",
                str(tmp_path / "note.json"),
            ]
        )
        == 2
    )
    assert "scout error" in capsys.readouterr().err
    assert not output.exists()
