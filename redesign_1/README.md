# Redesign 1: a rules-first agent that learns from every match

The agent plays the AWS Agentic Football Cup on the **real wire format**, based on the
AWS sample repo. It behaves like a football coach:

| Coach habit | Where in code | When |
|---|---|---|
| Fixed game plan the whole team follows | `agent/playbook.py` (17 bounded knobs) | always |
| Watch the opponent and adjust during the match | `agent/scout.py` (nudges with hysteresis) | every tick |
| Recognise a known opponent and prepare | `trainer/dossier.py` → pre-nudges at 30 s | from 30 s |
| Review our own mistakes after the match | `trainer/lessons.py` (error catalog) | after match |
| Try one change at a time and keep it only if it works | `trainer/league.py` (champion/challenger) | after match |
| Assistant coach's notes | `trainer/analyst.py` (optional Bedrock LLM) | after match |

The LLM is never on the tick path. Each decision is deterministic and takes under
1 ms. Learning only changes **bounded numbers**, so a bad lesson cannot break the
team, and a losing change is rolled back automatically.

## Layout

```
app.py     AgentCore entrypoint script (imports agent.main.app)
agent/     deployed to AgentCore (hot path, no network I/O)
  wire.py      payload <-> World, team-1 mirroring (we always attack +x internally)
  world.py     World / Pl model
  commands.py  command constructors + validation (only commands the server accepts)
  playbook.py  knobs with bounds/steps
  tactics.py   geometry, team roles (one presser, distinct markers, one interceptor)
  policy.py    World + Playbook -> one command + "why"
  events.py    snapshots and events (PASS_LOST, SHOT, TURNOVER, GOAL_AGAINST counter, ...)
  scout.py     in-match eagle eye: nudges + opponent fingerprint
  recorder.py  per-tick log, background flush to the store
  store.py     S3 (or in-memory) JSON store; failures never raise
  main.py      AgentCore entrypoint; playbook swap at kickoff; DECISION log line
trainer/   post-match "coach" (CLI or Lambda)
  ingest.py    merge the 5 runtime logs, replay them through the scout -> events + KPIs
  lessons.py   our recurring errors -> knob fixes (watch/ready/trial/adopted/hurt)
  dossier.py   opponent memory: fingerprint, tendencies, counter-nudges that worked
  analyst.py   optional LLM proposals (validated, clamped to one step)
  league.py    champion/challenger, circuit breaker, writes active.json
  run.py       orchestration, coach's notes, CLI, lambda_handler
tests/     unittest, no AWS needed
docs/      01-design (spec), 02-implementation-plan, 03-learning-loop, 04-runbook
```

## Quick start

```bash
cd redesign_1
python3 -m unittest discover -s tests -t .          # 34 tests, < 1 s
python3 -m trainer.run --dir /tmp/afc --no-llm      # train on a local folder
AFC_BUCKET=my-bucket python3 -m trainer.run         # train on S3 (all finished matches)
```

## Environment variables

| Var | Used by | Meaning |
|---|---|---|
| `AFC_BUCKET` | agent, trainer | S3 bucket. If unset, an in-memory store is used and nothing is learned across restarts |
| `AFC_PREFIX` | agent, trainer | key prefix, default `afc/` |
| `MY_PLAYER_ID` | agent | player id when the payload has no `myPlayers` |
| `AIM_FRAME` | agent | `attacker` (default) or `world`; see the runbook |
| `ANALYST_MODEL_ID` | trainer | Bedrock model id for the analyst; unset means no LLM |

See `docs/03-learning-loop.md` for how learning works and `docs/04-runbook.md` for
deployment and operations.
