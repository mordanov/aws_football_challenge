# Runbook

## Deploy the agent (AgentCore)

The entrypoint script is `app.py` (it imports `agent/main.py`: `app = BedrockAgentCoreApp()`, and the async
`invoke` yields `json.dumps([commands])`), the same shape as the AWS samples.

1. Package `redesign_1/` (`app.py`, `agent/`, `requirements.txt`).
2. Deploy one runtime per player with `MY_PLAYER_ID=0..4`, or one runtime for all
   players. Both work: `myPlayers` in the payload is honoured, and one command per
   player is returned.
3. Set `AFC_BUCKET` (and optionally `AFC_PREFIX`). The runtime role needs
   `s3:GetObject` on `active.json` and `dossiers/index.json`, and `s3:PutObject` on
   `matches/*`.
4. Check CloudWatch for one `DECISION {...}` line per tick: `why`, events, active
   nudges, `ms`.

## Deploy the trainer

- Lambda with handler `trainer.run.lambda_handler` (package `agent/` and `trainer/`
  together), on an EventBridge schedule every 5–10 minutes. It processes every match
  with no writes for 2 minutes and no `report.json`.
- Role: S3 read/write on the prefix; `bedrock:InvokeModel` only if
  `ANALYST_MODEL_ID` is set.
- Or run it by hand after practice matches: `AFC_BUCKET=... python3 -m trainer.run`.
  Re-processing one match: `--match <id> --force` (this counts the result again).

## First practice match: verify assumptions

The sample repo documents the schema but not every detail. After the first match:

| Assumption | How to check | If wrong |
|---|---|---|
| Aim labels are attacker-relative (L = attacker's left) | `SHOT` events in `report.json`: compare aim and outcome. Away shots should go where the aim says | set `AIM_FRAME=world` |
| `possessionAgentId` plus team resolution (ids repeat across teams) | `DECISION` logs: does our carrier get `shoot`/`pass` reasons? | adjust `wire._holder` |
| `duration` units (sample uses 3) | watch whether maintained commands (PRESS/MARK) persist | change the defaults in `commands.py` |
| No opponent name in the payload | grep the raw payload | if present, set `meta["opponent"]` in `main.py` and dossiers key by name |

To capture a raw payload, temporarily log `payload` in `main.invoke` for one tick.

## Daily operation

- Read `matches/<id>/report.json` → `coach_notes` for each match.
- `lessons.json` lists our errors and their status. `league.json` → `log` holds every
  promote/discard decision with the reason.
- **Freeze learning** before a tournament: stop the trainer schedule. `active.json`
  stays as it is.
- **Pin the champion**: write `{"name":"champion","version":N,"playbook":<champion.json>}`
  to `active.json` and stop the trainer.
- **Rollback**: copy `playbooks/history/v<NNNN>-champion.json` to
  `playbooks/champion.json` and `active.json`, then delete `playbooks/challenger.json`
  and set `"challenger": null` in `league.json`.
- **Reset**: delete `lessons.json`, `league.json`, `dossiers/` and `playbooks/`. The
  agent falls back to defaults.
