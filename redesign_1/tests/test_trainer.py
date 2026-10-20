import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent import commands as C  # noqa: E402
from agent.main import Brain  # noqa: E402
from agent.playbook import KNOBS, Playbook  # noqa: E402
from agent.store import MemoryStore  # noqa: E402
from tests.helpers import AWAY, HOME, payload, state  # noqa: E402
from trainer import analyst, dossier, ingest, league, run  # noqa: E402
from trainer import lessons as L  # noqa: E402


def summary(events=(), **kpis):
    k = {"gf": 0, "ga": 0, "gd": 0, "ticks": 150, "possession": 0.5, "shots": 0, "shots_on": 0, "passes": 0,
         "pass_lost": 0, "turnovers": 0, "turnovers_own": 0, "won_ball": 3, "won_opp_half": 1,
         "counter_goals_against": 0, "late_stamina": 0.6}
    k.update(kpis)
    return {"kpis": k, "events": list(events), "fingerprint": None, "scout_nudges": []}


def shot(dist, outcome="miss", clr=4.0):
    return {"type": "SHOT", "t": 1, "dist": dist, "outcome": outcome, "clr": clr}


class LessonsTest(unittest.TestCase):
    def test_long_shots_and_intercepted_passes_fire(self):
        s = summary([shot(30), shot(31), shot(28)], shots=3, passes=8, pass_lost=4)
        ids = {f["id"] for f in L.evaluate(s, [], Playbook())}
        self.assertIn("long_shots_waste", ids)
        self.assertIn("passes_intercepted", ids)
        self.assertNotIn("counter_conceded", ids)

    def test_long_shot_that_scored_does_not_fire(self):
        s = summary([shot(30), shot(31, "goal"), shot(28)], shots=3)
        self.assertNotIn("long_shots_waste", {f["id"] for f in L.evaluate(s, [], Playbook())})

    def test_ready_after_two_matches_or_once_if_strong(self):
        book = L.new_book()
        f = [{"id": "passes_intercepted", "evidence": "x"}, {"id": "counter_conceded", "evidence": "y"}]
        L.record(book, f, "m1")
        self.assertEqual(L.ready(book), ["counter_conceded"])
        L.record(book, f, "m2")
        self.assertEqual(set(L.ready(book)), {"counter_conceded", "passes_intercepted"})


def ready_book(*ids):
    book = L.new_book()
    for i in ids:
        book["lessons"][i] = {"status": "ready", "fired": 2, "fired_matches": [], "evidence": [], "hurt_until": 0,
                              "applied_versions": []}
    return book


class LeagueTest(unittest.TestCase):
    def test_challenger_bounded(self):
        book = ready_book(*[l.id for l in L.CATALOG])
        props = [{"knob": "tackle_range", "delta": 0.5, "reason": ""}]
        deltas, used, _ = league.plan_challenger(Playbook(), book, props)
        self.assertLessEqual(len(deltas), 3)
        self.assertTrue(used)
        pb = Playbook().with_nudges(deltas)
        for k, d in deltas.items():
            self.assertLessEqual(abs(d), KNOBS[k].step + 1e-9)
            self.assertTrue(KNOBS[k].lo <= pb.get(k) <= KNOBS[k].hi)

    def _play(self, st, book, gf, ga):
        act = st.get_json("active.json") or {"name": "champion", "version": 0}
        return league.record_and_plan(st, book, f"m{len((st.get_json('league.json') or {}).get('processed', []))}",
                                      (act["name"], act["version"]), gf, ga, [])

    def test_alternation_then_promotion(self):
        st, book = MemoryStore(), ready_book("passes_intercepted")
        d = self._play(st, book, 0, 1)                      # champion, opens trial v1
        self.assertEqual(d["next"], "challenger:1")
        played = []
        for _ in range(6):
            act = st.get_json("active.json")
            d = self._play(st, book, *((3, 0) if act["name"] == "challenger" else (1, 0)))
            played.append(d["played"])
            if "verdict" in d:
                break
        self.assertEqual(played[:2], ["challenger:1", "champion:0"])
        self.assertEqual(d["verdict"]["kind"], "promote")
        champ = Playbook.from_json(st.get_json("playbooks/champion.json"))
        self.assertEqual(champ.version, 1)
        self.assertGreater(champ.get("pass_lane_min"), KNOBS["pass_lane_min"].default)
        self.assertEqual(book["lessons"]["passes_intercepted"]["status"], "adopted")

    def test_losing_challenger_discarded_and_lessons_hurt(self):
        st, book = MemoryStore(), ready_book("passes_intercepted")
        self._play(st, book, 1, 0)
        d = None
        for _ in range(6):
            act = st.get_json("active.json")
            gf, ga = (0, 2) if act["name"] == "challenger" else (2, 0)
            d = self._play(st, book, gf, ga)
            if "verdict" in d:
                break
        self.assertEqual(d["verdict"]["kind"], "discard")
        self.assertEqual(book["lessons"]["passes_intercepted"]["status"], "hurt")
        self.assertEqual(Playbook.from_json(st.get_json("playbooks/champion.json")).version, 0)
        self.assertEqual(st.get_json("active.json")["name"], "champion")

    def test_circuit_breaker(self):
        st, book = MemoryStore(), ready_book("too_timid")
        self._play(st, book, 0, 0)
        d = self._play(st, book, 0, 4)
        self.assertEqual(d["verdict"]["kind"], "discard")
        self.assertIn("circuit breaker", d["verdict"]["why"])


class DossierAnalystTest(unittest.TestCase):
    FP = [0.5, 0.2, 0.1, 0.5, 0.4, 0.3, 0.3, 0.1, 0.4, 0.1, 0.05, 0.05]

    def test_nearest_neighbour_and_new_id(self):
        st = MemoryStore()
        a = dossier.update(st, self.FP, 1, ["line_high"], "m1")
        b = dossier.update(st, [v + 0.02 for v in self.FP], -1, ["line_high"], "m2")
        c = dossier.update(st, [1 - v for v in self.FP], 0, [], "m3")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        idx = st.get_json("dossiers/index.json")
        self.assertEqual(len(idx), 2)
        self.assertEqual(idx[0]["nudges"].get("through_bonus"), 15)

    def test_analyst_validation(self):
        pb = Playbook()
        self.assertEqual(analyst.propose({}, pb, lambda p: "no json here"), ([], ""))
        self.assertEqual(analyst.propose({}, pb, lambda p: 1 / 0), ([], ""))
        txt = json.dumps({"proposals": [{"knob": "bogus", "delta": 1}, {"knob": "shoot_range", "delta": 50},
                                        {"knob": "def_line", "delta": "x"}, {"knob": "tackle_range", "delta": True}],
                          "summary": "s"})
        props, notes = analyst.propose({}, pb, lambda p: "Sure!\n" + txt)
        self.assertEqual(props, [{"knob": "shoot_range", "delta": KNOBS["shoot_range"].step, "reason": ""}])
        self.assertEqual(notes, "s")

    def test_no_model_means_no_proposals(self):
        os.environ.pop("ANALYST_MODEL_ID", None)
        self.assertEqual(analyst.propose({}, Playbook()), ([], ""))


class EndToEndTest(unittest.TestCase):
    def test_runtimes_record_then_trainer_learns(self):
        st = MemoryStore()
        clock = [1_000_000.0]
        brains = {p: Brain(st, runtime=str(p), clock=lambda: clock[0], background=False) for p in range(5)}
        # we keep losing the ball on passes: our #2 has it, next tick the opponent has it
        for k in range(40):
            own = ("home", 2) if k % 2 == 0 else ("away", 3)
            gs = state(HOME, AWAY, ball=(-20, 5), owner=own, t=2.0 * k, tick=k, score=(0, k // 15))
            for p, b in brains.items():
                if own == ("home", 2) and p == 2:
                    b.handle(payload(gs, 0, [p]))
                    b.recorder.snaps[-1]["cmd"]["2"] = ["PASS", {"target_player_id": 3, "type": "GROUND"}]
                else:
                    b.handle(payload(gs, 0, [p]))
            clock[0] += 0.4  # runtimes start at slightly different wall-clock times
        for b in brains.values():
            b.recorder.join()
            b.recorder.flush()
        ids = ingest.match_ids(st)
        self.assertEqual(len(ingest.group_matches(ids)), 1)

        reports = run.train(st, now=clock[0] + 1000, use_llm=False)
        self.assertEqual(len(reports), 1)
        r = reports[0]
        self.assertEqual((r["kpis"]["gf"], r["kpis"]["ga"]), (0, 2))
        self.assertIn("passes_intercepted", {f["id"] for f in r["lessons_fired"]})
        self.assertIsNotNone(st.get_json(f"matches/{r['match']}/report.json"))
        self.assertIn("coach_notes", r)
        self.assertIsNotNone(st.get_json("active.json"))
        self.assertEqual(run.train(st, now=clock[0] + 1000, use_llm=False), [])   # nothing pending twice
        self.assertEqual(run.train(st, match_id=r["match"], use_llm=False), [])  # guarded re-count

    def test_commands_still_valid_after_learning(self):
        st = MemoryStore()
        st.put_json("active.json", {"playbook": Playbook({"shoot_range": 34, "tackle_range": 0}, 2, "challenger").to_json()})
        b = Brain(st, background=False)
        out = b.handle(payload(state(HOME, AWAY, owner=("away", 3), t=0), 0, [0, 1, 2, 3, 4]))
        self.assertTrue(all(C.validate(c) for c in out))
        self.assertEqual(b.base.name, "challenger")


if __name__ == "__main__":
    unittest.main()
