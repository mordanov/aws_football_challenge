# 01 - The Task

## The challenge (your description)
AWS Agentic AI Football Tournament: build an autonomous AI agent that controls
a football team in a simulated match. No manual play. The agent reads live
game state and returns actions in real time (positioning, passing, pressing,
scoring). Best tactics and best-engineered agent win.

## What public sources add (verified from articles, not from the game's own docs)
| Item | Detail | Source |
|---|---|---|
| Name | AWS Agentic Football Cup - Virtual League (Minds by Animoca Brands x AWS) | Animoca / agenticfootballcup.ai |
| Format | 5-a-side: GK, DEF, MID, FWD1, FWD2 | Strands blog, AWS Builder post |
| Players | Each player is its own agent, deployed in YOUR AWS account (Bedrock AgentCore); the platform calls your endpoints over HTTPS | Strands blog |
| Tick | Decision about every 2 s; all 10 agents called in parallel | Strands blog |
| Deadline | ~1 s per decision, end to end. Miss it and the player idles; no retries | Strands blog |
| Match length | ~120 s, roughly 300-600 invocations per team | AWS Builder post |
| Commands | MOVE_TO, PASS, SHOOT, DRIBBLE, PRESS_BALL, MARK, INTERCEPT, TACKLE / SLIDE_TACKLE, CLEAR, IDLE, GK_DISTRIBUTE, SET_STANCE | Strands blog, AWS Builder post, sample-agents repo |
| Coach channel | Mid-match text instructions; reported to accept only 6 presets (press_high, shoot_on_sight, slow_the_tempo, go_all_out_attack ...) | AWS Builder post |
| Tools | Strands Agents SDK, Bedrock (Nova, Claude, any Bedrock model), AgentCore, Kiro IDE | AWS pages |
| Practice | Three AWS bots to play against: balanced, attacking, defensive. Post-match report + coach advice | dev.to post |
| Tournament | Starts 11 Sep 2026, runs ~7 weeks, matches autoplay any time; each weekly tournament ends Sunday 23:59 UTC+8 | agenticfootballcup.ai |
| Prize | Finalists go to AWS re:Invent (Las Vegas, 30 Nov-4 Dec 2026); US$50,000 prize pool | Animoca / press |

## Success criteria
1. Win matches (goal difference), consistently, against unknown teams.
2. Never miss the 1 s deadline.
3. Never crash or emit an invalid command.

## Unknowns to resolve first (blockers)
- Exact game-state JSON field names, pitch size and origin.
- Exact command parameter names and shoot-aim labels.
- Scoring/ranking rules (points per win, tiebreaks).
- Whether your challenge is the same as the Cup above or a variant.

## Constraints that shape every design choice
- Stateless per tick by default (cold start / warm runtime unknown).
- Latency budget dominates: LLM calls are a liability, not a default.
- Agents cannot talk to each other; coordination must be implicit.
