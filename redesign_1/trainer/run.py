"""Post-match trainer: the 'coach' who reviews every match and updates the plan.

  python -m trainer.run                 # process every finished, unprocessed match in $AFC_BUCKET
  python -m trainer.run --match <id>    # (re)process one match
  python -m trainer.run --dir ./afc     # use a local folder instead of S3
Lambda: handler = trainer.run.lambda_handler (schedule it every 5-10 minutes).

Per match: merge recordings -> events/KPIs -> lessons -> dossier -> LLM analyst
-> champion/challenger -> report.json (human-readable coach's notes included).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.playbook import Playbook  # noqa: E402
from agent.store import MemoryStore, default_store  # noqa: E402

from . import analyst, dossier, ingest, league  # noqa: E402
from . import lessons as L  # noqa: E402

log = logging.getLogger("afc.trainer")


def train_one(store, ids: list[str], llm: Optional[analyst.Llm] = None, use_llm: bool = True) -> dict:
    mid = ids[0]
    timeline, meta = ingest.load_group(store, ids)
    if not timeline:
        return {"match": mid, "skipped": "empty"}
    summary = ingest.summarize(timeline)
    lg, champ, chal = league.load(store)
    played = (meta.get("playbook", "champion"), int(meta.get("version", 0) or 0))
    pb = chal if played[0] == "challenger" and chal is not None and chal.version == played[1] else champ

    fired = L.evaluate(summary, timeline, pb)
    book = store.get_json("lessons.json") or L.new_book()
    L.record(book, fired, mid)

    k = summary["kpis"]
    opp_key = dossier.update(store, summary["fingerprint"], k["gd"], summary["scout_nudges"], mid, meta.get("opponent"))
    opp = store.get_json(f"dossiers/{opp_key}.json") if opp_key else None

    report = {
        "match": mid, "ids": ids, "played": f"{played[0]}:{played[1]}", "kpis": k,
        "lessons_fired": [{"id": f["id"], "title": f["title"], "evidence": f["evidence"]} for f in fired],
        "scout_nudges": summary["scout_nudges"],
        "opponent": {"key": opp_key, "tendencies": (opp or {}).get("tendencies", []),
                     "record": (opp or {}).get("results", []), "counter_nudges": (opp or {}).get("counter_nudges", [])},
        "events": summary["events"][-200:],
    }
    proposals, notes = analyst.propose(report, pb, llm) if use_llm else ([], "")
    decision = league.record_and_plan(store, book, mid, played, k["gf"], k["ga"], proposals)
    store.put_json("lessons.json", book)

    report.update({"analyst": {"proposals": proposals, "summary": notes}, "league": decision,
                   "coach_notes": coach_notes(report, decision, notes)})
    store.put_json(f"matches/{mid}/report.json", report)
    return report


def coach_notes(report: dict, decision: dict, llm_notes: str) -> str:
    k = report["kpis"]
    lines = [f"{report['match']}: {k['gf']}-{k['ga']} playing {report['played']}. "
             f"Possession {k['possession']:.0%}, shots {k['shots']} ({k['shots_on']} on target), "
             f"passes lost {k['pass_lost']}/{k['passes']}, balls won {k['won_ball']}."]
    for f in report["lessons_fired"]:
        lines.append(f"- Our error: {f['title']} ({f['evidence']}).")
    opp = report["opponent"]
    if opp["key"]:
        lines.append(f"- Opponent {opp['key']}: {', '.join(opp['tendencies']) or 'no clear tendency yet'}; "
                     f"record vs them {opp['record']}.")
    if "verdict" in decision:
        v = decision["verdict"]
        lines.append(f"- Trial v{v['version']} {v['changes']}: {v['kind'].upper()} ({v['why']}).")
    if "new_challenger" in decision:
        nc = decision["new_challenger"]
        lines.append(f"- Next trial v{nc['version']}: {nc['changes']} because {'; '.join(nc['notes'])}.")
    lines.append(f"- Next match plays {decision['next']}.")
    if llm_notes:
        lines.append(f"- Analyst: {llm_notes}")
    return "\n".join(lines)


def pending_groups(store, now: Optional[float] = None) -> list[list[str]]:
    now = now or time.time()
    lg = store.get_json("league.json") or {}
    done = set(lg.get("processed", []))
    out = []
    for g in ingest.group_matches(ingest.match_ids(store)):
        if g[0] in done or any(store.get_json(f"matches/{m}/report.json") for m in g):
            continue
        metas = [store.get_json(k) for m in g for k in store.list(f"matches/{m}/meta-")]
        last = max((m.get("updated", 0) for m in metas if isinstance(m, dict)), default=0)
        if now - last >= ingest.FINISHED_AFTER_S:
            out.append(g)
    return out


def train(store=None, match_id: Optional[str] = None, llm: Optional[analyst.Llm] = None,
          now: Optional[float] = None, use_llm: bool = True, force: bool = False) -> list[dict]:
    store = store or default_store()
    if match_id:
        groups = [g for g in ingest.group_matches(ingest.match_ids(store)) if match_id in g] or [[match_id]]
        done = set((store.get_json("league.json") or {}).get("processed", []))
        if not force and groups[0][0] in done:
            log.info("%s already processed (use --force to count it again)", groups[0][0])
            return []
    else:
        groups = pending_groups(store, now)
    reports = []
    for g in groups:
        try:
            r = train_one(store, g, llm, use_llm)
            reports.append(r)
            log.info("%s", r.get("coach_notes", r))
        except Exception as e:  # noqa: BLE001
            log.exception("training on %s failed: %r", g, e)
    return reports


def lambda_handler(event, context):  # pragma: no cover - thin AWS wrapper
    logging.basicConfig(level=logging.INFO)
    reports = train(match_id=(event or {}).get("match_id"))
    return {"processed": [r.get("match") for r in reports]}


class DirStore(MemoryStore):
    """Local folder store (same layout as S3) for offline runs."""

    def __init__(self, root: str) -> None:
        super().__init__()
        self.root = root
        for d, _, files in os.walk(root):
            for f in files:
                p = os.path.join(d, f)
                self.data[os.path.relpath(p, root).replace(os.sep, "/")] = open(p, encoding="utf-8").read()

    def put_text(self, key: str, text: str) -> bool:
        super().put_text(key, text)
        p = os.path.join(self.root, key)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--match")
    ap.add_argument("--dir", help="local folder instead of S3")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--force", action="store_true", help="re-count an already processed match")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    store = DirStore(a.dir) if a.dir else default_store()
    reports = train(store, a.match, use_llm=not a.no_llm, force=a.force)
    print(json.dumps([r.get("coach_notes", r) for r in reports], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
