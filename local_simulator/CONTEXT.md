# Local football experiments

A local football simulation context for comparing frozen agent versions and
evaluating tactical-profile evolution under explicit simulator rules.

## Language

**Agent version**:
One of the five existing football-controller implementations, identified by its
source and fixed configuration. It is not necessarily a trained neural model.
_Avoid_: model, neural network.

**Frozen baseline**:
An agent version whose code, configuration and tactical profile remain unchanged
throughout a benchmark.
_Avoid_: evolving opponent, live learner.

**Patched entrant**:
A separate, versioned copy of an agent version with documented compatibility or
tactical fixes, frozen before scored evaluation. The source reference remains
unchanged.
_Avoid_: original version, silently repaired baseline.

**Comparison track**:
A separately reported benchmark population: patched entrants for the primary
ranking, or compatible original versions for the secondary ranking.
_Avoid_: mixed original-and-patched leaderboard.

**Benchmark**:
A controlled comparison of frozen baselines under a published simulator ruleset
and evaluation conditions. Its results do not establish AWS tournament strength.
_Avoid_: universal ranking, AWS validation.

**Ruleset**:
A versioned definition of the local game's mechanics and action semantics.
_Avoid_: verified AWS physics.

**Replay**:
An authoritative simulator recording of observations, attempted commands,
resolved actions and resulting match states.
_Avoid_: agent telemetry, inferred outcome.

**Candidate profile**:
A versioned set of redesign_4 tactical settings being evaluated for improvement
without changing the controller's source code.
_Avoid_: fine-tuned model, new neural weights.

**Evolution**:
Selection and variation of candidate profiles across experiments, followed by
independent evaluation against frozen opponents.
_Avoid_: LLM fine-tuning, automatic proven improvement.

**Walking skeleton**:
The approved end-to-end wiring check from a local command through trivial agents
and a recorded frame to browser replay display, before football mechanics exist.
_Avoid_: playable match, benchmark.
