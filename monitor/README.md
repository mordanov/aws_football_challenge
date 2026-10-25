# Training Monitor

Local web dashboard that reads the agent's S3 registry and shows learning progress in real time.

## Prerequisites

```bash
pip install flask boto3
```

AWS credentials must be configured (e.g. `aws configure` or an active SSO session) with read access to the agent's S3 bucket.

## Run

```bash
python3 -m monitor.monitor --config football_agent/deploy/s3_learning.json
```

Opens at **http://127.0.0.1:8765/**. Use `--port N` to change the port.

The config file is your deploy config (`s3_learning.json`, not `learning.json`). It needs `artifact_bucket`, `team_id`, and `account`.

## What to look at

**Agent Status** — the currently active profile. Key fields:

- *Generation* — how many parameter updates have been accepted so far. Stays at 1 until the first promotion.
- *press_radius* — the only auto-tuned parameter, shown with an "auto" badge. This is the number to watch.
- Other settings (pass_margin, shot_range, support_width) are fixed.

**Promotion History** — one row per generation, newest first.

| Status | Meaning |
|---|---|
| Promoted | This generation's parameters were accepted and became active |
| Rejected | Proposed change was tested but didn't improve results |
| No attempt | Baseline (gen 1) — learning hasn't proposed a change yet |

The *Changes* column shows what shifted when this generation was created (e.g. `press_radius 22.0 → 20.0`). If it's blank, no settings changed.

**Latest Attempt** — expands the top history row with a delta table. Useful for seeing whether `press_radius` moved up or down and by how much.

**Match Activity** — match counts per day for the last 8 days. A day with zero matches means the agent wasn't running or no observations landed in S3 that day.

## Auto-refresh

The page polls `/api/status` every 30 seconds. Use **Refresh now** to force an immediate update. The countdown resets after each fetch.

## Error banner

A red banner means the S3 fetch failed. Common causes: expired AWS credentials, wrong bucket name in the config, or no network access to S3. The previous data (if any) stays on screen.
