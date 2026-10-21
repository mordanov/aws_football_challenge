# Local Football Implementation Roadmap

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement an approved executable milestone plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a reproducible five-agent benchmark and then controlled redesign_4 profile evolution.

**Architecture:** A local deterministic engine collects isolated agents' decisions before resolving actions and records authoritative replay evidence. A CLI drives experiments; a browser reads saved replay artifacts. Frozen benchmark tracks and held-out profile evolution remain separate.

**Tech Stack:** Python 3.11+, local subprocesses, JSON evidence, browser-native HTML/SVG/JavaScript; CPU-only macOS target.

---

## Authority and scope

Read the approved
[system specification](../specs/2026-10-21-local-football-design.md) and
[glossary](../../../CONTEXT.md).

This roadmap covers the full specification. It is not an instruction to implement
unapproved physics. The separately detailed
[walking-skeleton plan](2026-10-21-local-football-skeleton.md) is the first
executable milestone. Author subsequent executable plans only after their input
contracts and design gates are approved.

Keep all five reference directories unchanged. Create isolated work at execution
time; do not install dependencies, copy agents or scaffold code while planning.

## Milestone dependencies

```text
M0 walking skeleton
  -> M1 command inventory and numerical rules approval
    -> M2 playable engine
      -> M3 frozen entrants and comparison harness
        -> M4 scored benchmark
          -> M5 profile evolution and independent final test
```

Replay improvements accompany M2/M3 but consume authoritative evidence only.
Do not make M4 depend on a live dashboard or local LLM.

## M0: walking skeleton

- [ ] Execute the separate skeleton plan after execution authorization.
- [ ] Demonstrate CLI -> two real local subprocesses -> trivial recorded frame ->
  browser-rendered saved artifact.
- [ ] Demonstrate repeatability and explicit failure, not gameplay or rankings.
- [ ] Review the working seams before changing their versioned contracts.

Spec coverage: section 3; thin slices of sections 4, 7, 8 and 11.

## M1: command inventory and rules approval

- [ ] Read each reference's parser, decision entry point, emitted commands,
  configuration and learning/state lifecycle without modifying it.
- [ ] Create `local_simulator/docs/agent-capabilities.md` with source-line evidence
  for observations, identity, units, roles, coordinates, output framing,
  commands/parameters, command persistence and state isolation.
- [ ] Classify each mismatch as an adapter translation, patched behavior or an
  unsupported semantic requiring a decision. Do not implement guessed mappings.
- [ ] Create `local_simulator/docs/rules-v1.md` specifying every numeric constant,
  random distribution, resolution order, action transition, goalkeeper/shot
  behavior, boundary restart and final-time condition.
- [ ] Specify how mirrored fixtures couple initial conditions and random streams.
- [ ] Present both documents for user approval before a playable-engine plan.
- [ ] Define engine state, action/event contracts and exact executable tasks in
  a new M2 plan based on those approved documents.

Exit gate: no unresolved ranked-command semantic; approved complete rules.
An entrant exclusion remains explicit. Primary-track completeness ultimately
requires all five patched entrants.

Spec coverage: sections 5 and 6; prerequisites for sections 7 and 12.

## M2: playable engine and authoritative replays

- [ ] First define typed state, observation, command, resolution and event
  interfaces from approved rules, independently of their implementation.
- [ ] Add public-boundary failing cases before each physics/action implementation.
- [ ] Implement fixed-step movement, stamina, ball travel, ownership, action
  resolution, scoring, automatic restarts and finite match termination.
- [ ] Verify simultaneous observations and explicit persistent/one-shot actions.
- [ ] Verify mirrored mechanics, deterministic seeded reruns and finite/bounded
  state, including ties and final-step goal/ball-flight cases.
- [ ] Version the richer replay schema; never label skeleton artifacts as matches.
- [ ] Extend the viewer to time seek, pause/play, speed, decision stepping, player
  selection and attempted-versus-resolved evidence with no agent execution.
- [ ] Measure headless throughput, replay size and memory on the target Mac.

Exit gate: complete rules coverage and a reproducible playable match; no ranking.

Spec coverage: sections 4, 6, 7, 8, 11 and engine acceptance in section 12.

## M3: entrants and benchmark harness

- [ ] Define entrant, adapter, fixture, attempt, manifest and campaign interfaces.
- [ ] Create versioned patched copies under `local_simulator/`; maintain source
  hashes, diffs, a fix ledger and focused regression evidence.
- [ ] Integrate all five sources using their approved boundary mappings and
  preserve documented per-player/team-shared process topology.
- [ ] Disable learning, seed entrant randomness and reset match-local state.
- [ ] Run separate patched and compatible-original development comparisons;
  report original exclusions rather than rewriting those references.
- [ ] Add bounded transport, classified failures, strict validation, manifests
  and verified resume without duplicate fixtures or unrecorded retries.
- [ ] Define paired-seed round-robin scheduling and deterministic report contracts.
- [ ] Verify scoring and block-bootstrap calculations with known fixture datasets.
- [ ] Lock entrants, adapters, rules, dependencies, budgets and scored seeds.

Exit gate: all five primary entrants pass integration; no scored-set tuning.

Spec coverage: sections 4, 5, 7, 8, 9 and integration acceptance in section 12.

## M4: scored benchmark and report

- [ ] Run 2,000 primary-track fixtures: 10 pairs x 100 seed blocks x two legs.
- [ ] Run a separate original track with its declared compatible population.
- [ ] Verify every fixture's terminal status and immutable inputs before reporting
  a complete campaign; operational failures keep the result provisional.
- [ ] Publish standings, head-to-heads, failures, rejection counts, uncertainty,
  replay references and configuration/source identities.
- [ ] Separate points leader from a multiplicity-adjusted winner; report
  inconclusive or cyclic outcomes honestly.
- [ ] Preserve the primary entrants as immutable Stage 2 opponents.

Exit gate: complete reproducible benchmark, or explicit provisional status with
no overall-winner claim. A new patch requires a new revision and campaign.

Spec coverage: section 9 and benchmark acceptance in section 12.

## M5: profile evolution

- [ ] Define profile, lineage, seed-pool, fitness and promotion-evidence interfaces.
- [ ] Reuse the frozen redesign_4 controller and its actual profile validator;
  record all four parameters' bounds rather than creating parallel validators.
- [ ] Enforce disjoint development/training/selection/final pools and frozen
  opponents. Do not reuse Stage 1 scored seeds for Stage 2 evaluations.
- [ ] Implement the specified population of 12, maximum 10 generations, mutation
  and incumbent retention with reproducible lineage and exact-input caching.
- [ ] Evaluate up to three distinct training challengers plus the incumbent on
  selection fixtures; freeze exactly one finalist before final-test access.
- [ ] Evaluate finalist/control on matched final fixtures and apply the aggregate
  improvement and per-opponent -0.10 non-regression criteria.
- [ ] Publish rejection or promotion without modifying source references or
  pretending that profile search updates model weights.

Exit gate: auditable correct experiment. No improvement is an acceptable result.

Spec coverage: section 10 and evolution acceptance in section 12.

## Cross-cutting release checks

- [ ] Reference preservation: compare all five directories against the execution
  baseline; investigate any difference before finishing a milestone.
- [ ] Offline operation: no runtime cloud credentials, CDN, analytics or
  dependency downloads.
- [ ] Documentation: update vocabulary when domain terms change and bootstrap
  simulator context/state/data-flow documents once code establishes those flows.
- [ ] Report exact validation and measurement results; never claim AWS parity,
  unmeasured throughput or statistically unsupported superiority.

## Coverage and authorization

Specification sections 1-2 constrain every milestone; section 3 maps to M0;
4-8 map to M1-M3; 9 maps to M3-M4; 10 maps to M5; 11 maps to M0/M2;
12 is enforced by the milestone exits; 13 remains an explicit authorization gate.

The roadmap has no authorization to invent physics or to start execution.
After M0, review M1's concrete rules and command inventory before planning M2.
