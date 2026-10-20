# State machines - learning football team

## Possession

**States:** free, known holder (team/index), unknown.
**Initial:** decoded from each authoritative observation.
**Transitions:** any state to another on the next snapshot; explicit free wins
over a stale holder, ambiguous reused IDs remain unknown.
**Source:** `agent/protocol.py`, `decode`.

## Runtime decision

**States:** received, normalized, planned, validated, recorded, returned; error.
**Initial:** received.
**Transitions:** normalize -> plan -> validate -> record -> return. Recoverable
errors with known team/player identity record a diagnostic and return validated
balanced stance. Unrecoverable identity errors fail rather than invent IDs.
**Source:** `agent/runtime.py`, `Runtime.handle`.

## Imported match

**States:** absent, partial, complete.
**Initial:** absent.
**Transitions:** absent -> partial or complete on valid transactional import;
partial -> complete only with unchanged identity/time/profile. Re-import adds
nonconflicting records or is idempotent. Invalid/conflicting input rolls back.
Complete results cannot be silently revised or downgraded.
**Source:** `agent/trainer.py`, `Trainer.ingest`.

## Tactical learning

**States:** observations, proposals, bounded candidate, approved, exported, deployed.
**Initial:** observations.
**Transitions:** import -> evidence-qualified proposals; training -> bounded
candidate when thresholds permit, otherwise baseline general settings;
explicit `promote` -> durable operator approval event; explicit export ->
atomic JSON (export also permits unapproved candidates for evaluation);
operator deployment -> pinned runtime.
Rollback exports an older stored candidate. Deployment is an operational step,
not implemented infrastructure. No transition proves a tactical improvement.
**Source:** `agent/trainer.py`, `Trainer.train`/`export`; `agent/profile.py`.
