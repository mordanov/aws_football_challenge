from __future__ import annotations

import json
from pathlib import Path

import pytest

from football_lab import cli_campaign
from football_lab.cli_campaign import _coach_fn, _load_entrant_spec, _runner_env, main
from football_lab.coaches import scripted_coach


@pytest.fixture
def fake_entrants(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "entrants" / "foo"
    (d / "profiles").mkdir(parents=True)
    (d / "profiles" / "p.json").write_text("{}")
    (d / "entrant.json").write_text(json.dumps({
        "id": "foo_v1", "source_name": "foo", "topology": "per_player",
        "shim": "entrants/foo/shim.py", "entrant_hash": "0" * 64,
        "rules_version": "v1", "track": "patched"}))
    (d / "variants.json").write_text(json.dumps({"base_id": "foo_v1", "variants": [
        {"id": "foo_a", "profile": "profiles/p.json", "coach": "scripted"},
        {"id": "foo_bad", "profile": "profiles/p.json", "coach": "nope"}]}))
    monkeypatch.setattr(cli_campaign, "_ENTRANTS_DIR", tmp_path / "entrants")
    return d


def test_variant_resolution_env_and_coach(fake_entrants: Path) -> None:
    base = _load_entrant_spec("foo_v1")
    assert "profile" not in base and _runner_env(base) is None and _coach_fn(base) is None
    v = _load_entrant_spec("foo_a")
    assert v["id"] == "foo_a" and v["source_name"] == "foo"
    assert v["profile"] == str((fake_entrants / "profiles" / "p.json").resolve())
    assert _runner_env(v) == {"AFC_PROFILE": v["profile"]}
    assert _coach_fn(v) is scripted_coach
    with pytest.raises(ValueError):
        _coach_fn(_load_entrant_spec("foo_bad"))
    with pytest.raises(ValueError):
        _load_entrant_spec("missing")


@pytest.mark.parametrize("argv", [
    [],                                                    # no mode
    ["--entrants", "foo_v1", "--challengers", "foo_a"],   # both modes
    ["--challengers", "foo_a"],                            # pool missing
    ["--entrants", "foo_v1,foo_a", "--foul-scale", "2"],  # foul scale needs v2
    ["--challengers", "foo_a", "--pool", "foo_a"],         # overlap
])
def test_init_rejects_bad_arguments(fake_entrants: Path, tmp_path: Path,
                                     argv: list[str]) -> None:
    assert main(["init", "--output", str(tmp_path / "m.json"), *argv]) == 2


def test_init_cross_v2(fake_entrants: Path, tmp_path: Path) -> None:
    out = tmp_path / "m.json"
    assert main(["init", "--challengers", "foo_a", "--pool", "foo_v1", "--rules", "v2",
                 "--foul-scale", "0.5", "--blocks", "2", "--output", str(out)]) == 0
    m = json.loads(out.read_text())
    assert m["rules_version"] == "v2" and m["rules_params"]["foul_scale"] == 0.5
    assert m["challengers"] == ["foo_a"] and len(m["fixtures"]) == 4


def test_end_to_end_v2_campaign(tmp_path: Path) -> None:
    manifest, report = tmp_path / "m.json", tmp_path / "r.json"
    assert main(["init", "--entrants", "redesign_4_v1,initial_idea_v1", "--rules", "v2",
                 "--blocks", "1", "--seed-pool-start", "5000",
                 "--output", str(manifest)]) == 0
    assert main(["run", "--manifest", str(manifest), "--replays", str(tmp_path / "rp"),
                 "--discard-replays"]) == 0
    assert not list((tmp_path / "rp").glob("*.json"))
    assert main(["report", "--manifest", str(manifest), "--json", str(report),
                 "--baseline", "initial_idea_v1"]) == 0
    r = json.loads(report.read_text())
    assert r["rules_version"] == "v2" and r["complete"] == 2 and r["final"] is True
    assert {p["entrant_id"] for p in r["pooled_goal_diff"]} == {"redesign_4_v1",
                                                                "initial_idea_v1"}
    assert set(r["discipline"]) == {"redesign_4_v1", "initial_idea_v1"}
    # A 2-team round robin has no shared (opponent, block) units, so no paired delta.
    assert r["goal_diff_delta_vs_baseline"] == {"baseline": "initial_idea_v1", "deltas": {}}
    results = json.loads(manifest.read_text())["results"]
    assert all(res["replay_path"] is None and "discipline" in res for res in results)
