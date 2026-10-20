# The learning loop: "learn from every match"

```
            during the match (each runtime, ~every 2 s)                 between matches (trainer)
 payload -> World -> Scout.observe ----> nudges ----+                 matches/<id>/*.jsonl
                          |                         v                         |
                          |          Playbook(base) + nudges -> policy        v
                          |                                         ingest: merge + replay through Scout
                          +--> Recorder -> S3 matches/<id>/...             |   events + KPIs + fingerprint
                                                                          v
 active.json <---- league (champion / challenger) <---- lessons (our errors) + analyst (LLM, optional)
 dossiers/index.json <------------------------------ dossier (opponent memory)
```

## 1. During the match: the eagle eye (`agent/scout.py`)

Each runtime watches the opponent on every tick. All five see the same state, so
they reach the same conclusions without talking to each other.

| What the scout sees | What changes (bounded, additive) |
|---|---|
| Their keeper is more than 8 m off his line for 5 ticks | shoot from further out, harder |
| We lost 2 or more passes | demand cleaner passing lanes |
| 65 % or more of their carries go down one flank | shift DEF/MID toward that flank |
| Their outfield line pushes into our half | more through balls and aerials over the top |
| Deep block | wait for clearer shots, push the shape up slightly |
| 2 or more goals conceded on the counter | drop the defensive line, team shape back |

Each signal has **hysteresis**: it turns on after N confirmations and off after N
contrary observations, so one odd tick does not flip the tactics. Nudges change
individual behaviour only. Team roles (who presses, who marks) use the base playbook,
so the five runtimes always agree.

At **30 s** the scout's fingerprint of the opponent (12 numbers) is compared with
known dossiers. On a match, the counter-nudges that worked against that opponent
before are applied straight away.

## 2. After the match: the trainer (`trainer/run.py`)

1. **Ingest.** Merge the per-runtime logs (one match can appear as several ids; ids
   of the same team started within 90 s are grouped). Replay the merged timeline
   through the *same* Scout and event code that ran in the match.
2. **Lessons (our errors).** Each catalog entry is a detector plus a small fix:

   | Lesson | Error | Fix |
   |---|---|---|
   | long_shots_waste | 3 or more long shots, 0 goals | shoot_range -2 |
   | shots_blocked | half or more of shots fail through bodies | shot_clear_min +0.3 |
   | passes_intercepted | more than 30 % of passes lost | pass_lane_min +0.3, pass_max_len -3 |
   | passing_too_safe | under 10 % lost, under 3 shots | pass_lane_min -0.3 |
   | too_timid | ball near goal, under 2 shots | shoot_range +2, shot_clear_min -0.3 |
   | counter_conceded (strong) | goal conceded on a counter | def_line -0.05, press_height -0.1 |
   | own_third_turnovers | 3 or more losses in own third | pass_lane_min +0.3, gk_kick_min -5 |
   | gk_dist_lost | 2 or more keeper distributions lost | gk_kick_min +5 |
   | stamina_collapse | mean stamina under 0.25 in the last minute | sprint_stamina_min +0.05 |
   | press_futile | high press wins nothing | press_height -0.1 |
   | no_threat | 55 % or more possession, 1 shot or fewer | shape_shift +0.05 |

   Lifecycle in `lessons.json`: `watch` → (fired in 2 matches, or 1 if strong)
   `ready` → `trial` (inside a challenger) → `adopted` (challenger promoted) or
   `hurt` (challenger lost; suppressed for 5 matches). The notebook therefore
   records what went wrong, what we tried, and whether it helped.
3. **Dossier (opponent memory).** The fingerprint is matched to the nearest dossier,
   or a new one is created. The dossier stores tendencies in plain words (e.g.
   "keeper leaves his line", "attacks down their left"), results against that
   opponent, and statistics on which scout nudges were active in better or worse
   results. Nudges that were usually needed and did not hurt become the dossier's
   counter-nudges.
4. **Analyst (optional).** Bedrock reads the report and proposes up to 3 knob
   changes. Anything invalid is dropped, and every delta is clamped to one step.
   Lessons take priority over the analyst.
5. **League (champion/challenger).** When no trial is running, the trainer builds
   a challenger from the champion plus ready lessons plus analyst proposals. It
   changes at most 3 knobs, each by at most one step. Matches alternate between
   champion and challenger. After 3 challenger matches:
   - **promote** if its mean goal difference is better, or equal with more goals
     for;
   - otherwise **discard**, and its lessons become `hurt`.

   A challenger that concedes 4 or more goals in a match is discarded immediately
   (circuit breaker).
6. **Report.** `matches/<id>/report.json` contains KPIs, events, lessons fired, the
   opponent dossier, the league decision and readable **coach's notes**, e.g.:

   ```
   t0-...: 1-2 playing champion:0. Possession 67%, shots 0, passes lost 19/39.
   - Our error: Passes intercepted (lost 19/39 passes).
   - Our error: Sterile possession (67% possession but 0 shots).
   - Opponent opp-001: attacks down their right (our left, +y); record vs them [-1].
   - Next trial v1: {...} because lesson passes_intercepted ...
   ```

## 3. Safety rails

- Every knob has hard bounds. Nudges and lessons are clamped.
- A new playbook is applied only at kickoff, never mid-match.
- S3 or LLM failures are logged and never raised. Without S3 the agent still plays
  with defaults.
- Rollback: copy any `playbooks/history/v<NNNN>-*.json` to `playbooks/champion.json`
  and `active.json`.
