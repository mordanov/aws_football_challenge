#!/usr/bin/env python3
"""Rebuild the redesign_4_v2 entrant from ../../../redesign_4.

Run from local_simulator: ../.venv/bin/python entrants/redesign_4_v2/build.py
Rebuild (and commit the result) whenever redesign_4/agent changes.
"""
from __future__ import annotations

import json
import shutil
import sys
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIM = HERE.parents[1]
SOURCE = SIM.parent / "redesign_4"
sys.path.insert(0, str(SOURCE))
sys.path.insert(0, str(SIM / "src"))

from agent.archetypes import ARCHETYPES, STAND_INS  # noqa: E402
from agent.model import Profile  # noqa: E402
from agent.profile import FORMATIONS, parse_profile  # noqa: E402

from football_lab.evolution.integrity import hash_tree  # noqa: E402

PREFIX = "r4v2"


def variants() -> list[tuple[str, Profile, str | None]]:
    out: list[tuple[str, Profile, str | None]] = []
    for name, base in ARCHETYPES.items():
        for policy in ("none", "rules"):
            for formation in FORMATIONS:
                suffix = "" if formation == base.style.formation else "_f" + formation.replace("-", "")
                vid = f"{PREFIX}_{name}_{policy}{suffix}"
                style = replace(base.style, formation=formation)
                out.append((vid, replace(base, version=vid, coach_policy=policy, style=style), None))
    vid = f"{PREFIX}_balanced_extcoach"
    out.append((vid, replace(ARCHETYPES["balanced"], version=vid), "scripted"))
    for name, profile in STAND_INS.items():
        vid = f"{PREFIX}_{name}"
        out.append((vid, replace(profile, version=vid), None))
    return out


def main() -> None:
    for generated in ("agent", "profiles"):
        shutil.rmtree(HERE / generated, ignore_errors=True)
    (HERE / "entrant.json").unlink(missing_ok=True)
    shutil.copytree(
        SOURCE / "agent", HERE / "agent",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "advisor"),
    )
    shutil.copy2(SIM / "entrants" / "redesign_4" / "profile_shim.py", HERE / "shim.py")
    (HERE / "profiles").mkdir()
    rows = []
    for vid, profile, coach in variants():
        data = parse_profile(profile.to_dict()).to_dict()
        (HERE / "profiles" / f"{vid}.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        rows.append({"id": vid, "profile": f"profiles/{vid}.json", "coach": coach})
    (HERE / "variants.json").write_text(
        json.dumps({"base_id": "redesign_4_v2", "variants": rows}, indent=1) + "\n"
    )
    entrant = {
        "id": "redesign_4_v2",
        "source_name": "redesign_4",
        "topology": "per_player",
        "shim": "entrants/redesign_4_v2/shim.py",
        "entrant_hash": hash_tree(HERE),  # everything except entrant.json (removed above)
        "rules_version": "v1",  # entrant protocol version; match rules come from the campaign
        "track": "patched",
        "patches": [{
            "id": "profile_env",
            "classification": "compatibility",
            "file": "shim.py",
            "description": "profile loaded from $AFC_PROFILE (set per variant by the campaign CLI)",
        }],
    }
    (HERE / "entrant.json").write_text(json.dumps(entrant, indent=2) + "\n")
    print(f"built {len(rows)} variants; entrant_hash={entrant['entrant_hash'][:12]}")


if __name__ == "__main__":
    main()
