# Redesign 1 Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans. Steps use `- [ ]`.

**Goal:** Implement `redesign_1/docs/01-design.md`: a rules-first agent on the real AFC wire
format that learns in-match (scout) and across matches (trainer: lessons, dossiers,
champion/challenger, LLM analyst), persisted in S3.

**Architecture:** `agent/` (hot path, no I/O) + `trainer/` (post-match). Shared
`agent/events.py`. Storage behind a `Store` protocol (S3 or in-memory).

**Tech stack:** Python 3.10+ stdlib; `boto3` optional (lazy import); `bedrock_agentcore`
optional. Tests: `unittest` (`python3 -m unittest discover -s tests` from `redesign_1/`).

Assumptions: Python >= 3.10 available locally; no AWS needed for tests.

---

## Contracts (Task 0)

```python
# agent/world.py
@dataclass(frozen=True) class Pl: id:int; x:float; y:float; vx=0.; vy=0.; stamina=1.; sprinting=False; last_action=""
@dataclass class World:
    me: Pl; mates: list[Pl]; opps: list[Pl]; ball:(x,y); ball_vel:(vx,vy)
    owner: tuple[str,int]|None   # ("us"|"them", idx) ; None = free
    score_us:int; score_them:int; game_time:float; tick:int; play_mode:str; mode_team_us:bool|None
    team_id:int; s:int  # s=+1 if raw attacks +x; internal coords are multiplied by s (we attack +x)
    coach:str; match_len=300.; length=110.; width=70.
# agent/wire.py
parse_payload(payload:dict, default_pid:int|None) -> tuple[dict gameState, int team_id, list[int] pids]  # all of myPlayers
parse_world(game_state:dict, team_id:int, pid:int) -> World
to_wire(cmd:dict, w:World) -> dict          # adds teamId, un-mirrors target_x/target_y
validate(cmd:dict) -> bool
# agent/playbook.py
KNOBS: dict[name, Knob(default, lo, hi, step)]
@dataclass Playbook: values:dict; version:int=0; name="champion"
    get(k); clamp(); with_nudges(nudges:dict[str,float]) -> Playbook; to_json(); from_json()
# agent/events.py
detect(prev:Snapshot, cur:Snapshot) -> list[dict]   # Snapshot = compact dict (recorder format)
snapshot(w:World, cmds:dict[int,dict]) -> dict
# agent/scout.py
class Scout: observe(snapshot) -> events ; nudges() -> dict ; active() -> list[str] ; fingerprint() -> list[float] ; reset()
# agent/store.py
class Store(Protocol): get_json(key)->dict|None ; put_json(key,obj) ; put_text(key,str) ; list(prefix)->list[str] ; get_text(key)->str|None
class MemoryStore ; class S3Store ; def default_store() -> Store|None
# agent/policy.py
decide(w:World, pb:Playbook, team_pb:Playbook|None) -> Decision(cmd, why)  # team_pb = base, for coordination
# trainer
ingest.group_matches(ids) ; ingest.load_group(store, ids) -> (timeline, meta) ; ingest.summarize(timeline)
lessons.evaluate(summary, timeline, pb) -> list[fired] ; lessons.record/ready/mark(book, ...)
dossier.update(store, fp, gd, active_nudges, match_id, name=None) -> key ; analyst.propose(report, pb, llm=None) -> (proposals, summary)
league.record_and_plan(store, book, match_id, played, gf, ga, proposals) -> decision ; run.train(store, match_id=None, llm=None) -> [report]
```

## Tasks

- [x] **T1 world+wire.** Tests: sample AWS state parses (home & away mirrored), prompt as JSON
  string, possession resolved by team, old format, garbage -> error. `to_wire` un-mirrors and adds teamId.
- [x] **T2 playbook.** Tests: defaults within bounds; clamp; with_nudges clamps; json round-trip.
- [x] **T3 tactics+policy.** Tests: open shot -> SHOOT aim in {TL,TR,BL,BR}; blocked -> no SHOOT;
  1 presser + distinct markers across 5 pids; GK with ball -> GK_DISTRIBUTE; all valid; < 50 ms.
- [x] **T4 events.** Tests: PASS_LOST, SHOT goal, GOAL_AGAINST counter, TURNOVER, WON_BALL.
- [x] **T5 scout.** Tests: GK-off-line nudge after 5 ticks only; pass-lost nudge; flank bias; fingerprint length.
- [x] **T6 store+recorder.** Tests: MemoryStore round-trip; recorder detects new match (gameTime goes back),
  flushes jsonl to `matches/<id>/p<N>.jsonl`; store failures swallowed.
- [x] **T7 main.** Tests: handle() returns valid JSON list for all 5 pids; garbage -> SET_STANCE; playbook
  swap only at kickoff; DECISION line printed.
- [x] **T8 trainer: ingest+lessons.** Tests: synthetic timeline fires long_shots_waste & passes_intercepted.
- [x] **T9 trainer: dossier+analyst.** Tests: nearest-neighbour match / new id; analyst rejects bad JSON,
  unknown knob, clamps delta to one step.
- [x] **T10 trainer: league+run.** Tests: challenger <= 3 knobs in bounds; alternation; promotion; discard
  marks lessons hurt; circuit breaker at >= 4 conceded; end-to-end `train()` on MemoryStore.
- [x] **T11 docs.** README (layout, run, deploy notes, env vars), 03-learning-loop.md, 04-runbook.md.
- [x] **T12 verify.** Full suite green; commit.
