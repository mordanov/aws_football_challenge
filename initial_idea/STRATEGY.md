# Strategy: "Coordinated Code, Advisory LLM"

## What the research says (and where it comes from)

Facts below are from the Strands blog "Inside Agentic Football Cup", the AWS
Builder Center post "72 Hours of Agentic Football", and the public
`agentic-football-sample-agents` layout. I could not open the sample repo
itself (GitHub blocked automated access), so the exact game-state field names
are NOT confirmed.

- 5v5 (GK, DEF, MID, FWD1, FWD2), ~120 s match, a decision tick about every 2 s.
- Each player is a separate agent; all see the same state; ~1 s hard limit
  (slow = player idles that tick; ~900 ms is the risk line).
- Teams that rely on one command type (usually PASS) lose; command diversity wins.
- Prompts alone are ignored often (one team: 207 of 370 commands were MOVE_TO,
  0 were MARK despite the prompt demanding it). Deterministic overrides fixed it.
- Long-range hoofs (45 m+) were 92-98% of one team's shots and gave away
  possession. Shoot only from range with a clear lane.
- A team once played 0% LLM decisions (pure fallback) without noticing.
  Log the decision source every tick.
- Shorter history and capped output tokens cut latency from ~1.5 s to ~0.7 s.
- Mid-match coach channel accepts only 6 presets (e.g. press_high,
  shoot_on_sight, slow_the_tempo, go_all_out_attack); free text got a 400.

## The edge

Most teams write five clever prompts. Their weakness is that five stateless
agents cannot talk, so they mob the ball, leave runners unmarked, and miss
ticks on timeouts. This agent beats that with:

1. **Implicit coordination.** All five agents run the same deterministic
   function on the same snapshot, so they agree without messaging:
   one presser (nearest eligible outfielder, nearest-ID tiebreak), distinct
   markers on the most advanced off-ball opponents, one interceptor.
   No mobbing, no gaps.
2. **Code pulls the trigger.** Shots, tackles, keeper actions and shape are
   rules (<1 ms). The LLM is only asked on genuine close calls for the ball
   carrier, picks an index from options already validated, and has a hard
   0.55 s budget. It cannot emit an illegal move or cause a timeout.
3. **Shot discipline.** Shoot only inside ~33 m with a clear lane, aiming at
   the freest corner. Lanes shut: cut back or pass, never hoof.
4. **Counter-attack.** When 3+ opponent outfielders sit in our half, the
   best forward with no defender goal-side gets a through ball (+30 bonus).
5. **Game-state posture.** Late deficit: shoot range up, shape pushed up.
   Late lead: drop shape, slow tempo. Coach order changes posture too.
6. **Exploit the opponent's rhythm.** Decisions land every ~2 s; opponent
   LLMs often idle on timeouts. Compact shape plus a designated presser
   punishes slow teams.

## What to do first (highest value order)

1. Capture a real game-state payload from a practice match (the three
   practice bots: balanced, attacking, defensive) and make
   `agent/adapter.py` lookups exact. This is the biggest risk in the project.
2. Confirm command names/parameters in the workshop BUILD_AND_DEPLOY.md and
   fix `agent/commands.py` (aim labels, MARK/PASS parameter names).
3. Run `observe` loop: read the match report, tune the few constants in
   `tactics.py` (shoot range, marker count, anchors).
4. Only then enable the LLM (`LLM_ENABLED=1`) and A/B it against rules-only.

## Open questions I could not verify
- Pitch size/origin (assumed 110x70 m, centered; override with env vars).
- Whether SHOOT aim is LEFT/CENTER/RIGHT and which side is which.
- Whether the real wire format matches my command envelope.
- Whether the rules-only agent beats the practice bots. I have only
  unit-tested the logic, not played a match.
