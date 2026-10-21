# Local football simulator and evolution league

Date: 2026-10-21

Status: Written specification approved by the user on 2026-10-21. Implementation
planning is authorized; simulator implementation requires a separate decision.

## 1. Purpose and limits

Build a fully local, reproducible football environment for two purposes:

1. Evaluate tactical-profile evolution under explicit local game rules.
2. Compare the five existing agent versions and identify the strongest supported
   entrants under those rules, including inconclusive or cyclic results.

The user accepts that local results may not transfer to AWS football. Exact AWS
physics, protocol semantics and deployment behavior remain unverified. Reports
must not describe simulator wins as AWS validation.

Target: the user's Apple M1 Pro Mac with 32 GB RAM, CPU-only. No AWS account,
credentials, Floci, Docker, GPU, local LLM or network access is required during
experiments. Dependency installation is a separate setup operation.

All new project work belongs in `local_simulator/`. Preserve `initial_idea/`,
`redesign_1/`, `redesign_2/`, `redesign_3/` and `redesign_4/` unchanged.

Canonical vocabulary is in [CONTEXT.md](../../../CONTEXT.md).

## 2. Approved delivery stages

**Stage 1: benchmark.** Deliver a deterministic headless engine, isolated agent
execution, compatibility adapters, two separately labeled comparison tracks,
saved replays, a CLI and a browser replay viewer.

**Stage 2: evolution.** Evolve redesign_4 tactical profiles against the five frozen
primary-track opponents. Require separate training, selection and final-test
evidence. Do not evolve other controllers or update neural weights.

Stage 2 depends on an accepted Stage 1 engine and benchmark. Never mix benchmark
fixtures with learning runs.

Out of scope: cloud deployment, AWS emulation, LLM coaching or action selection,
neural-policy training, LLM fine-tuning, a live dashboard, multiplayer networking
and a production-grade football physics engine.

## 3. First milestone: walking skeleton

This wiring check precedes football mechanics and real-agent integration.

1. A CLI command starts a seeded two-team run with trivial test agents.
2. Separate local agent processes receive one hardcoded observation and return
   fixed commands shaped according to the provisional transport contract.
3. A trivial step records the commands and one hardcoded next frame.
4. The run saves an artifact and exits with its location and explicit status.
5. The browser viewer loads that artifact and displays the frame and commands.

The seams are observation-to-command, state-plus-simultaneous-commands-to-frame,
record-to-artifact and artifact-to-browser-display. Define versioned request,
response and replay contracts during this milestone; later mechanics plug into
them without bypassing the agent boundary or recorder.

Acceptance: one end-to-end check launches the path, verifies saved commands and
frame, and confirms browser rendering. The same seed reproduces the same
semantic replay content. Run IDs, filesystem locations and wall-clock metadata
are outside that deterministic comparison. This milestone is not a playable
match and must not generate rankings.

## 4. Runtime and isolation

Use a local Python implementation with a CLI. Agent execution uses local
subprocesses and an explicit, versioned request/response transport. The replay
viewer uses browser-native rendering and locally available assets.

Launch a fresh isolated team runtime for each match and reset all match-local
state. Each runtime exposes decisions for its five players. Document and preserve
whether an entrant uses per-player or team-shared state; do not accidentally
change its architecture by sharing module globals.

Give every player the same authoritative pre-action world state, transformed
only by its documented observation adapter. Collect both teams' commands before
advancing the world. No player observes another player's same-round command or
an already-updated state. Output arrival order and process scheduling must not
affect action resolution or random draws.

Seed any entrant-owned randomness as well as the engine. If an original cannot
be made reproducible through configuration alone, disclose and exclude it from
the reproducible original track; a necessary seeding patch is a patched revision.

Continuous actions persist according to the ruleset; one-shot commands must not
execute repeatedly just because the physics runs faster than decisions.

Each agent version may use its own interpreter/environment where necessary.
Package-name collisions between the five `agent` implementations must not cross
process boundaries. Transport stdout contains protocol data only; diagnostics
use a separate stream and are captured with bounded size.

Declare startup, request and output-size watchdog limits in the run manifest.
Freeze them for a comparison campaign. These protect the machine; this benchmark
does not claim to reproduce AWS latency or enforce a one-second cloud SLA.

## 5. Entrants, patches and fairness

The five source versions are `initial_idea`, `redesign_1`, `redesign_2`,
`redesign_3` and `redesign_4`. They are agent implementations, not five trained
neural models.

### Primary track: patched, frozen entrants

The user permits compatibility and tactical fixes before freezing. Apply them
only to separate copies under `local_simulator/`, never to the source references.
Every patched entrant must retain an identifiable parent version.

Maintain a fix ledger containing the defect, compatibility/tactical
classification, changed behavior, regression evidence, source diff and revision.
Record development fixtures and tuning effort. The report must acknowledge
unequal tuning effort; patched rankings are not claims about untouched originals.

Use only development seeds to diagnose, fix or tune. Freeze source, adapter,
dependencies, configuration, tactical profile and rules before scored fixtures.
Any later change creates a new revision and requires a fresh comparable campaign
on a new evaluation seed pool.

### Secondary track: untouched originals

Run compatible originals through compatibility-only adapters. No tactical edits
or silent reduced-action policy is allowed in this track. List excluded versions,
unsupported capabilities and reasons. With fewer than five entrants, call this a
partial original-version comparison, not an answer about all five originals.

### Shared requirements

Disable automatic learning and between-match adaptation in both tracks. Pin all
profiles for the campaign. A necessary behavior-changing learning-disable patch
belongs in the patched track; do not label that artifact an untouched original.

Adapters may translate envelopes, identifiers, coordinates, units and command
representations without improving decisions. Tactical fixes are separate from
adapters. Unsupported semantics must be visible in the capability matrix.

Record source/configuration/profile/adapter hashes and dependencies so the exact
entrant can be reconstructed. Never combine original and patched track scores
into one leaderboard.

## 6. Football ruleset v1

These are proposed local mechanics, not reconstructed AWS facts:

| Property | Contract |
|---|---|
| Teams | Two teams, five players each |
| Roles | GK, DEF, MID, FWD1, FWD2 |
| Pitch | Centered 110 by 70 coordinate space |
| Match time | 120 simulated seconds |
| Physics step | 0.05 simulated seconds, integer step counter |
| Decisions | Every 2 simulated seconds, beginning at time zero |
| Observations | Full shared world observation, no hidden information advantage |
| Team capabilities | Identical physical capabilities and stamina rules |
| Orientation | Opposite attack directions; no halftime switching |
| Randomness | Recorded seeded streams, independent of process scheduling |

Include movement with acceleration and speed limits, possession, ball travel,
passing, shooting, goalkeeping, stamina, pressure, marking, interceptions, goals,
kickoffs and boundary restarts. A possessing player carries the ball while
moving; no invented DRIBBLE command is required.

Use simplified automatic boundary restarts rather than set-piece tactics.
Exclude offsides, injuries, cards, substitutions and detailed body-contact
physics. Define any treatment of aerial passes and shot-height labels explicitly;
do not silently flatten distinct commands into equivalent actions.

Goal and match-finalization events cannot be bypassed by observation timing.
Define the exact treatment of simultaneous contacts, goal-line crossings,
possession transitions, match-end boundaries and interrupted ball flight.

### Mandatory rules-completion gate

This system specification deliberately does not invent unverified numerical
football mechanics. Before scored evaluation, the playable-engine milestone must
produce and obtain approval for:

1. A complete versioned rules document with numeric parameters and deterministic
   resolution procedures for movement, ball travel, possession contests, shots,
   goalkeeper effects, stamina, boundaries and restarts.
2. A capability matrix covering every command emitted by each frozen entrant,
   including eligibility, parameters, coordinate interpretation, persistence,
   conflicts, rejection reasons and any semantic approximation.
3. Evidence that team exchange plus pitch mirroring preserves game mechanics
   for correspondingly mirrored initial states, commands and random streams.
   Agents need not choose symmetric tactics.

An approximation that changes an entrant's intended command semantics requires
explicit approval before that entrant is ranked. Unsupported commands cannot
silently become movement, successful actions or generic no-ops.

The gate is a required design deliverable of Stage 1, not permission to leave
mechanics unspecified in a scored run. Engine constants and command semantics
must be locked and hashed before evaluation seeds are opened.

## 7. Determinism and authoritative evidence

Separate attempted decisions, validation results, accepted actions, resulting
events and state transitions. Agent explanations are optional agent claims, not
authoritative causes.

Record at least:

- Run/match identities, track, both entrants and their immutable hashes.
- Simulator/rules/replay schema versions, seeds, configuration and watchdogs.
- Initial state, observations, attempted commands and acceptance/rejection.
- Physics frames, ball/player state, possession, stamina, clock and score.
- Authoritative events linking action attempts to actual outcomes where known.
- Final result or explicit failure status, diagnostic counters and timing data.

Possession distinguishes a free ball from an owned ball. Owner identity contains
both team and player, avoiding collisions between reused player IDs.

A pass in flight is not a completed pass. Record launch, interception,
completion, boundary exit or unresolved-at-end separately. Never infer a scorer,
completion or successful action merely from an attempted command.

Version random-number algorithms and stream derivation. Use integer physics
steps for ordering and stable tie-breaking rather than iteration/process order.
Same rules, seed, entrants and deterministic environment reproduce semantic
events and frames on the supported platform. Do not promise cross-platform
bitwise floating-point identity.

Save append-only match evidence with an explicit terminal status. Publish final
campaign summaries atomically after completeness checks. Interrupted output
remains identifiable as partial. Never mix retries or count one fixture twice.

## 8. Failure policy

Protocol failures, crashes, output-limit violations and watchdog timeouts abort
the fixture and are reported as operational failures, not fabricated 0-0 results
or simulated goals.

A gameplay-invalid command is instead an explicit rejection under the locked
ruleset, with that command's effect defined there. The rejected command cannot
silently succeed, erase unrelated valid actions or trigger an undocumented
fallback.

Distinguish agent failures, adapter incompatibility and engine/infrastructure
faults. Retry only through an explicit recorded attempt; do not replace an
unfavorable valid outcome. Any failed, missing or unresolved fixture keeps the
campaign provisional and blocks a final overall-winner claim.

Do not treat subprocess execution as a security sandbox. Run only these trusted
local agent sources, use local disposable work directories and bounded logs, and
avoid passing real AWS credentials into child environments.

## 9. Stage 1 benchmark

Within each track, run every unordered entrant pair using 100 seed blocks. Each
block contains two legs with team sides swapped and a mirrored initial setup.
Streams must be coupled deliberately under mirroring, not accidentally depend on
home-team processing order.

For five entrants: 10 pairings x 100 blocks x 2 legs = 2,000 matches.
Seed pools are independent between development and scored campaigns. Record
seed-generation and allocation rules before running.

Primary standings: mean league points per match, with win 3, draw 1, loss 0.
Break display ties by mean goal difference, then display remaining equals as
tied. Do not impose a source-name tiebreaker that implies playing superiority.

Publish games, wins/draws/losses, points, goals, goal difference, per-opponent
results, command rejections, operational failures and timing distributions.
Provide fixture-level evidence and links to saved replays.

Compute uncertainty by resampling complete two-leg seed blocks, stratified by
matchup; do not count frames, players or ticks as independent matches. Use a
recorded bootstrap seed and 10,000 resamples. Display ordinary 95% intervals,
with their finite-sample/Monte Carlo limitations.

Distinguish the points leader from a statistically established winner. For a
five-entrant campaign, a winner claim additionally requires a positive lower
bound on head-to-head points advantage against every rival using two-sided
99.5% bootstrap intervals: a conservative Bonferroni adjustment over all 10
pairwise comparisons at a nominal familywise 5% level. For smaller original
tracks, use their actual pair count. These are approximate uncertainty
assessments, not mathematical proof of superiority.

Allow inconclusive, tied and cyclic outcomes. Do not extend the seed budget
after inspecting results to obtain significance; a larger follow-up is a new
preregistered campaign.

## 10. Stage 2 profile evolution

Freeze the Stage 1 rules and all five primary-track opponents. Their source,
profiles and behavior must not change during training or final testing.
The evolution controller and incumbent are the frozen primary-track redesign_4
entrant, including its documented patches, not a silently substituted original.
Only candidate profiles vary.

Vary only redesign_4's existing `pass_margin`, `shot_range`, `press_radius` and
`support_width`, within the bounds enforced by its frozen profile validator.
Copy the actual bound values into the experiment manifest and validate every
candidate before running it. No source edits or arbitrary new tactical knobs.

Use four non-overlapping seed pools:

| Pool | Purpose |
|---|---|
| Development | Engine, adapter and patch work; never scored |
| Training | Fitness and mutation selection |
| Selection | Choose a single finalist without final-test access |
| Final test | One locked promotion decision against the incumbent |

All three Stage 2 evaluation pools are also disjoint from Stage 1 scored seeds.
Development evidence remains separate from every scored pool.

Initial budget: population 12, at most 10 generations including the initial
population. Include the unchanged redesign_4 incumbent. Rank training fitness by
mean league points, then goal difference, against all five frozen opponents.
Use 8 paired seed blocks per opponent for training evaluations and the same
fixtures for every candidate in that experiment.

Generate the initial 11 variants from the incumbent. Later generations preserve
the incumbent and the previous generation's best profile, filling the remaining
10 places with mutations of the top three profiles. Resolve exact selection ties
by stable profile hash, explicitly as a search rule rather than a strength claim.
Choose mutation parents uniformly from those three with recorded seeded draws.
Duplicate profiles may reuse fixture evidence only when every evaluation input
is identical; disclose actual unique evaluations.

For mutation, independently select each parameter with probability 0.5 and ensure
at least one is selected by uniformly choosing one if the selection is empty.
Perturb selected values by a seeded zero-mean Gaussian
with standard deviation 10% of the valid parameter range, clamp to valid bounds,
and apply the existing validator. Record parents, mutation seed, resulting
settings and evidence. This is bounded parameter search, not model fine-tuning.

Take at most three distinct non-incumbent profiles with the best training fitness
across all generations. Evaluate them and the incumbent on 20 paired selection
blocks per opponent. Select by mean points, then goal difference; exact ties
favor the incumbent, then stable profile hash. Select exactly one finalist
before opening final-test data. If the incumbent remains best, record no
promotion and do not manufacture a candidate.

Evaluate the selected candidate and incumbent against the same five frozen
opponents using 100 paired final-test blocks per opponent: 1,000 matches per
evaluated profile. Keep both legs and candidate/control results grouped within
each resampled seed block. Use 10,000 recorded-seed bootstrap resamples.

Promotion requires all of:

- A positive lower bound on the candidate-minus-incumbent aggregate mean
  points difference using a two-sided 95% bootstrap interval.
- Against each opponent, a lower bound of at least -0.10 points per match using
  two-sided 99% intervals, adjusting the five non-regression checks by Bonferroni.
- Complete valid evidence, unchanged opponents and no unresolved run failures.

The -0.10 margin is an explicit tolerance, not a claim of zero regression. The
same approximate-inference caveats as Stage 1 apply. A candidate can fail even
when its observed mean is higher. Never tune or select another candidate against
an already-opened final-test pool; use a new experiment and pool.

Record promotion or rejection, lineage and evidence. Promotion changes the
selected profile artifact only; it does not overwrite an original source
directory, opponent, prior replay or baseline.

## 11. CLI and replay viewer

The CLI supports running a match, running/resuming an explicitly identified
campaign, producing a report, running a profile-evolution experiment and opening
replay evidence. Command spellings are not fixed by this specification.

Use immutable run identities and configuration hashes. Resume skips only verified
completed fixtures whose inputs match exactly; corrupt or partial fixtures are
reported, not silently accepted.

The approved viewer layout has:

- Pitch, score, simulated time, team names and playback controls as the main area.
- Pause/play, time seek, previous/next decision and playback speed.
- A side panel for selected-player attempts, accepted/rejected actions and events.
- Optional player IDs, targets, trajectories and emitted agent explanations.
- Track, seed, rules version, entrant/profile identities and failure diagnostics.

The viewer consumes saved artifacts without rerunning agents or modifying state.
Distinguish in-flight actions from completed outcomes. Render recorded text as
text, not executable HTML. Bundle assets locally; no CDN or telemetry dependency.
It is a replay tool, not a live training dashboard.

The approved illustrative mockup was reviewed through the local visual companion;
its positions and score were not experimental results.

## 12. Acceptance and verification

| Gate | Required evidence |
|---|---|
| Wiring | Approved walking-skeleton end-to-end path and browser rendering |
| Rules | Complete approved mechanics document and five-entrant capability matrix |
| Engine | Deterministic reruns, mirrored-side symmetry, finite termination, valid ownership, bounded/finite state and action semantics |
| Integration | All five patched entrants run without unresolved protocol incompatibilities; originals and exclusions identified separately |
| Benchmark | Complete locked primary round robin, reconstructible reports/replays, no learning leakage and no unsupported winner claim |
| Evolution | Reproducible lineage, validated bounds, frozen opponents, independent final-test decision and auditable rejection/promotion |
| Preservation | The five reference source directories remain unchanged |

Add focused automated checks for deterministic replay, simultaneous observation,
action eligibility and persistence, ball-flight outcomes, score/restart/time
boundaries, malformed agent output, failures, artifact completeness and seed-pool
separation. Exercise real subprocess boundaries, not only mocked decision calls.
Verify browser playback against recorded frames.

Verify statistical calculations using small known fixture datasets and guard
against treating correlated legs or ticks as independent samples.

Measure match throughput, wall time, peak memory and replay size on the target
Mac after representative integration. Do not promise an unmeasured rate.
Training runs are headless and need not wait for simulated real time; viewer
playback is independent. Performance work must preserve semantics and evidence.

Stage 2 passes when the experiment is correct and auditable, even if it finds no
improvement. A compatible engine plus honest negative evidence is a valid
research result.

## 13. Next authorization boundary

Review this written system specification before implementation planning.
Implementation starts only after explicit approval. Detailed football mechanics
are a separate mandatory approval gate before any scored benchmark.
