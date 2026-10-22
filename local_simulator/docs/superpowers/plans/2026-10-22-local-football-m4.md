# Local Football M4: Scored Benchmark Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the full 2,000-fixture primary-track benchmark (10 pairs × 100 seed blocks × 2 legs),
extend the report with head-to-head tables, operational summaries, and a Bonferroni-adjusted winner
claim, and emit the final campaign report.

**Architecture:** All new code lives in `campaign_report.py` and `cli_campaign.py`.
`bootstrap_ci` gains a `confidence` parameter; three new functions cover head-to-head,
operational summary and the winner claim. The benchmark is run from the CLI into
`local_simulator/local/benchmark/`. No new modules.

**Tech Stack:** Python 3.11+, existing `campaign.py` / `campaign_report.py` / `cli_campaign.py`,
`pytest`, `ruff`, `mypy`.

**Spec:** `docs/superpowers/specs/2026-10-21-local-football-design.md` section 9.

## Global Constraints

- All new project work in `local_simulator/`. Preserve `initial_idea/`, `redesign_1/`,
  `redesign_2/`, `redesign_3/`, `redesign_4/` unchanged.
- Scored seed pool: `seed_pool_start=10000`. Development seeds (`0`–`9999`) must not
  overlap with scored fixtures.
- Winner claim uses two-sided **99.5% bootstrap CI** across all 10 pairs (Bonferroni:
  10 comparisons at nominal 5% familywise → 0.5% per test → 99.5% CI).
  A positive lower bound against every rival is required.
- 10,000 bootstrap resamples with a recorded seed.
- Operational failures keep the campaign provisional; no overall-winner claim is made.
- All commands run from `local_simulator/` unless stated otherwise.
- `ruff check src/ tests/` and `mypy src/football_lab/` must be clean after every task.

## Review Focus

The five input classes the spec implies but no task's tests fully exercise:

1. **Cyclic outcome** (A beats B, B beats C, C beats A): `winner_claim` must return
   `winner_id=None` with `reason="inconclusive"` even when one entrant leads on points.
2. **All 99.5% lower bounds exactly zero**: not a winner — requires `lo > 0`, not `lo >= 0`.
3. **Operational failure in one fixture of a pair**: standings skip that fixture;
   the campaign is provisional; no winner claim is made.
4. **Block with only one leg complete** (other failed): bootstrap must not resample
   that partial block as if it were a full two-leg block.
5. **Entrant appears as both team0 and team1 across legs**: rejection counts must sum
   correctly regardless of which side the entrant played.

Each concern is pinned to the task that owns the code — see the test steps below.

---

## File structure

- Modify: `src/football_lab/campaign_report.py` — extend `bootstrap_ci` + add three functions
- Modify: `src/football_lab/cli_campaign.py` — extend `_cmd_report`
- Modify: `tests/test_campaign_report.py` — new tests for new functions
- Benchmark output (git-ignored via `local/`): `local/benchmark/`

---

### Task 1: Head-to-head matrix and operational summary

**Files:**
- Modify: `src/football_lab/campaign_report.py`
- Modify: `tests/test_campaign_report.py`

**Interfaces:**
- Produces:
  ```python
  class HeadToHeadRecord(TypedDict):
      wins: int; draws: int; losses: int; goals_for: int; goals_against: int

  def head_to_head_matrix(
      results: list[FixtureResult],
  ) -> dict[str, dict[str, HeadToHeadRecord]]:
      """Returns {entrant_a: {entrant_b: record}} from a's perspective."""

  class OperationalSummary(TypedDict):
      total: int; complete: int; failed: int

  def operational_summary(
      manifest: CampaignManifest,
  ) -> tuple[OperationalSummary, dict[str, int]]:
      """Returns (summary, rejections_by_entrant_id)."""
  ```

- [ ] **Step 1: Write failing tests**

  ```python
  # test_campaign_report.py — append

  _H2H_RESULTS = [
      _r("a__vs__b__b0000__l0", "a", "b", [2, 0]),  # a wins
      _r("b__vs__a__b0000__l1", "b", "a", [1, 0]),  # b wins
      _r("a__vs__b__b0001__l0", "a", "b", [1, 1]),  # draw
      _r("b__vs__a__b0001__l1", "b", "a", [1, 1]),  # draw
  ]

  def test_head_to_head_wins():
      h = head_to_head_matrix(_H2H_RESULTS)
      assert h["a"]["b"]["wins"] == 1
      assert h["a"]["b"]["losses"] == 1
      assert h["a"]["b"]["draws"] == 2

  def test_head_to_head_goals():
      h = head_to_head_matrix(_H2H_RESULTS)
      assert h["a"]["b"]["goals_for"] == 4   # 2+1+1 as team0, +0 as team1
      assert h["a"]["b"]["goals_against"] == 3  # 0+1+1 as team0, +1 as team1

  def test_head_to_head_symmetry():
      h = head_to_head_matrix(_H2H_RESULTS)
      assert h["a"]["b"]["wins"] == h["b"]["a"]["losses"]
      assert h["a"]["b"]["goals_for"] == h["b"]["a"]["goals_against"]

  def test_operational_summary_complete():
      # build a minimal manifest with 3 fixtures, 2 complete, 1 failed
      from football_lab.campaign import round_robin_fixtures
      from football_lab.entrant_contracts import CampaignManifest
      fixtures = round_robin_fixtures(["a", "b"], seed_pool_start=0, n_blocks=1)
      # add a third fake fixture
      extra = dict(fixtures[0])
      extra["fixture_id"] = "a__vs__b__b0001__l0"
      fixtures_3 = fixtures + [extra]
      manifest: CampaignManifest = {
          "campaign_id": "t", "track": "t", "entrants": [],
          "seed_pool_start": 0,
          "budget": {"startup_timeout_s": 5.0, "request_timeout_s": 5.0, "output_size_limit": 65536},
          "rules_version": "v1", "locked": False,
          "fixtures": fixtures_3,
          "results": [
              {"fixture_id": fixtures_3[0]["fixture_id"], "status": "complete",
               "score": [1, 0], "failure_reason": None, "replay_path": None,
               "duration_ms": 10.0, "team0_rejections": 2, "team1_rejections": 1},
              {"fixture_id": fixtures_3[1]["fixture_id"], "status": "failed",
               "score": [0, 0], "failure_reason": "timeout", "replay_path": None,
               "duration_ms": 10.0, "team0_rejections": 0, "team1_rejections": 0},
          ],
      }
      summary, rejections = operational_summary(manifest)
      assert summary["total"] == 3
      assert summary["complete"] == 1
      assert summary["failed"] == 1

  def test_operational_summary_rejections():
      # rejections are summed per entrant regardless of home/away side
      from football_lab.campaign import round_robin_fixtures
      from football_lab.entrant_contracts import CampaignManifest
      fixtures = round_robin_fixtures(["a", "b"], seed_pool_start=0, n_blocks=1)
      manifest: CampaignManifest = {
          "campaign_id": "t", "track": "t", "entrants": [],
          "seed_pool_start": 0,
          "budget": {"startup_timeout_s": 5.0, "request_timeout_s": 5.0, "output_size_limit": 65536},
          "rules_version": "v1", "locked": False,
          "fixtures": fixtures,
          "results": [
              {"fixture_id": f["fixture_id"], "status": "complete",
               "score": [0, 0], "failure_reason": None, "replay_path": None,
               "duration_ms": 10.0,
               "team0_rejections": 5, "team1_rejections": 3}
              for f in fixtures
          ],
      }
      _summary, rejections = operational_summary(manifest)
      # a is team0 in leg0 → 5 rejections, team1 in leg1 → 3 rejections
      assert rejections["a"] == 8
      assert rejections["b"] == 8
  ```

- [ ] **Step 2: Run tests to confirm they fail**

  Run: `PYTHONPATH=src pytest tests/test_campaign_report.py -k "head_to_head or operational" -v`
  Expected: 5 failures (ImportError or AttributeError — functions don't exist yet)

- [ ] **Step 3: Implement `HeadToHeadRecord`, `OperationalSummary`, `head_to_head_matrix`, `operational_summary` in `campaign_report.py`**

  `head_to_head_matrix`: iterate complete results; for each result decode fixture_id to get team0/team1; update both directions' wins/draws/losses/goals.

  `operational_summary`: count fixtures in manifest vs results; sum `team0_rejections` (attributed to `team0_entrant`) and `team1_rejections` (attributed to `team1_entrant`) by looking up fixture spec from manifest.

  Add `HeadToHeadRecord` and `OperationalSummary` as `TypedDict`s at the top of the file.
  Import `CampaignManifest` from `.entrant_contracts`.

- [ ] **Step 4: Run tests to confirm they pass**

  Run: `PYTHONPATH=src pytest tests/test_campaign_report.py -k "head_to_head or operational" -v`
  Expected: 5 pass

- [ ] **Step 5: Full suite + quality checks**

  Run: `PYTHONPATH=src pytest tests/test_campaign_report.py -v`
  Expected: all pass

  Run: `ruff check src/ tests/ && mypy src/football_lab/`
  Expected: clean

- [ ] **Step 6: Commit**

  ```bash
  git add src/football_lab/campaign_report.py tests/test_campaign_report.py
  git commit -m "feat(M4): add head-to-head matrix and operational summary"
  ```

---

### Task 2: Winner claim (configurable CI + Bonferroni test)

**Files:**
- Modify: `src/football_lab/campaign_report.py`
- Modify: `tests/test_campaign_report.py`

**Interfaces:**
- Consumes: `bootstrap_ci` from Task 1's file state
- Produces:
  ```python
  class WinnerClaimResult(TypedDict):
      winner_id: str | None   # None if inconclusive
      is_conclusive: bool
      reason: str             # "clear_winner" | "inconclusive" | "incomplete_data"
      intervals_99_5: dict[str, dict[str, tuple[float, float]]]

  def bootstrap_ci(
      results: list[FixtureResult],
      rng_seed: int,
      n_resamples: int = 10_000,
      confidence: float = 0.95,          # NEW parameter
  ) -> dict[str, dict[str, tuple[float, float]]]: ...

  def winner_claim(
      results: list[FixtureResult],
      entrant_ids: list[str],
      bootstrap_seed: int,
      n_resamples: int = 10_000,
  ) -> WinnerClaimResult: ...
  ```

- [ ] **Step 1: Write failing tests**

  ```python
  # test_campaign_report.py — append

  def test_bootstrap_ci_confidence_99_5():
      # 10 blocks, a always wins → any confidence should give lo > 0
      results = (
          [_r(f"a__vs__b__b{i:04d}__l0", "a", "b", [3, 0]) for i in range(10)]
          + [_r(f"b__vs__a__b{i:04d}__l1", "b", "a", [0, 3]) for i in range(10)]
      )
      ci_95  = bootstrap_ci(results, rng_seed=0, confidence=0.95)
      ci_995 = bootstrap_ci(results, rng_seed=0, confidence=0.995)
      lo_95,  hi_95  = ci_95["a"]["vs_b"]
      lo_995, hi_995 = ci_995["a"]["vs_b"]
      # 99.5% interval is wider than 95%
      assert lo_995 <= lo_95
      assert hi_995 >= hi_95
      # both still positive (a always wins)
      assert lo_995 > 0

  def test_winner_claim_clear_winner():
      # a wins every match against b and c; b and c draw every match
      results = (
          [_r(f"a__vs__b__b{i:04d}__l0", "a", "b", [2, 0]) for i in range(20)]
          + [_r(f"b__vs__a__b{i:04d}__l1", "b", "a", [0, 2]) for i in range(20)]
          + [_r(f"a__vs__c__b{i:04d}__l0", "a", "c", [2, 0]) for i in range(20)]
          + [_r(f"c__vs__a__b{i:04d}__l1", "c", "a", [0, 2]) for i in range(20)]
          + [_r(f"b__vs__c__b{i:04d}__l0", "b", "c", [1, 1]) for i in range(20)]
          + [_r(f"c__vs__b__b{i:04d}__l1", "c", "b", [1, 1]) for i in range(20)]
      )
      claim = winner_claim(results, ["a", "b", "c"], bootstrap_seed=0, n_resamples=500)
      assert claim["winner_id"] == "a"
      assert claim["is_conclusive"] is True
      assert claim["reason"] == "clear_winner"

  def test_winner_claim_inconclusive():
      # Only 2 blocks — too little data for positive lower bound at 99.5%
      results = (
          [_r(f"a__vs__b__b{i:04d}__l0", "a", "b", [2, 0]) for i in range(2)]
          + [_r(f"b__vs__a__b{i:04d}__l1", "b", "a", [1, 0]) for i in range(2)]
      )
      claim = winner_claim(results, ["a", "b"], bootstrap_seed=0, n_resamples=500)
      assert claim["winner_id"] is None
      assert claim["is_conclusive"] is False

  def test_winner_claim_incomplete_data():
      # Only 1 of 3 pairs has results → incomplete_data
      results = [_r("a__vs__b__b0000__l0", "a", "b", [2, 0])]
      claim = winner_claim(results, ["a", "b", "c"], bootstrap_seed=0)
      assert claim["reason"] == "incomplete_data"
      assert claim["winner_id"] is None

  def test_winner_claim_operational_failure_provisional():
      # A failed fixture → incomplete_data (campaign provisional)
      results = [
          _r("a__vs__b__b0000__l0", "a", "b", [2, 0]),
          {"fixture_id": "a__vs__b__b0000__l1", "status": "failed",
           "score": [0, 0], "failure_reason": "timeout", "replay_path": None,
           "duration_ms": 0.0, "team0_rejections": 0, "team1_rejections": 0},
      ]
      claim = winner_claim(results, ["a", "b"], bootstrap_seed=0)
      assert claim["reason"] == "incomplete_data"
  ```

- [ ] **Step 2: Run tests to confirm they fail**

  Run: `PYTHONPATH=src pytest tests/test_campaign_report.py -k "confidence or winner_claim" -v`
  Expected: 5 failures

- [ ] **Step 3: Implement `confidence` parameter on `bootstrap_ci` and `winner_claim`**

  `bootstrap_ci`: replace hardcoded `0.025`/`0.975` with `alpha = (1 - confidence) / 2`;
  use `int(alpha * n_resamples)` and `int((1 - alpha) * n_resamples) - 1`.

  `winner_claim`:
  1. Check every pair in `itertools.combinations(sorted(entrant_ids), 2)` appears in complete results; if any missing or any result has `status != "complete"` → return `reason="incomplete_data"`.
  2. Call `bootstrap_ci(results, bootstrap_seed, n_resamples, confidence=0.995)`.
  3. For each entrant, check `ci[e][f"vs_{rival}"][0] > 0` for all rivals; collect candidates.
  4. If exactly one candidate: `winner_id=candidate, is_conclusive=True, reason="clear_winner"`.
  5. Otherwise: `winner_id=None, is_conclusive=False, reason="inconclusive"`.

- [ ] **Step 4: Run tests to confirm they pass**

  Run: `PYTHONPATH=src pytest tests/test_campaign_report.py -k "confidence or winner_claim" -v`
  Expected: 5 pass

- [ ] **Step 5: Full suite + quality checks**

  Run: `PYTHONPATH=src pytest tests/test_campaign_report.py -v`
  Expected: all pass

  Run: `ruff check src/ tests/ && mypy src/football_lab/`
  Expected: clean

- [ ] **Step 6: Commit**

  ```bash
  git add src/football_lab/campaign_report.py tests/test_campaign_report.py
  git commit -m "feat(M4): add configurable bootstrap CI and winner claim"
  ```

---

### Task 3: Extended CLI report

**Files:**
- Modify: `src/football_lab/cli_campaign.py`
- Modify: `tests/test_campaign_report.py` (one integration smoke test)

**Interfaces:**
- Consumes: `head_to_head_matrix`, `operational_summary`, `winner_claim` from Tasks 1–2
- Produces: extended `_cmd_report` output format (stdout)

New report sections (in order):

```
Campaign: 2000/2000 complete [FINAL]        ← or PROVISIONAL if any failed

Standings
Entrant              G    W    D    L   Pts   GF   GA   GD
------------------------------------------------------------
...

Head-to-head (wins – draws – losses  GF:GA)
             a        b        c        d        e
a            –       20-5-15  ...
b           15-5-20  –        ...
...

Operational summary
Entrant              Rejections   Failures
------------------------------------------
...

Pairwise 95% CI (per-match point differential)
...

Winner claim (99.5% CI, Bonferroni over 10 pairs)
  Winner: redesign_4_v1   [or INCONCLUSIVE / PROVISIONAL]
  Reasoning: positive lower bound vs all 4 rivals
```

- [ ] **Step 1: Write a CLI smoke test**

  ```python
  # tests/test_campaign_report.py — append

  def test_cmd_report_sections(tmp_path, capsys):
      """_cmd_report output contains all required sections."""
      import argparse
      from football_lab.campaign import round_robin_fixtures, save_manifest
      from football_lab.cli_campaign import _cmd_report
      from football_lab.entrant_contracts import CampaignManifest
      fixtures = round_robin_fixtures(["a", "b"], seed_pool_start=0, n_blocks=2)
      manifest: CampaignManifest = {
          "campaign_id": "t", "track": "primary", "entrants": [],
          "seed_pool_start": 0,
          "budget": {"startup_timeout_s": 5.0, "request_timeout_s": 5.0, "output_size_limit": 65536},
          "rules_version": "v1", "locked": False,
          "fixtures": fixtures,
          "results": [
              {"fixture_id": f["fixture_id"], "status": "complete",
               "score": [1, 0] if f["leg"] == 0 else [0, 1],
               "failure_reason": None, "replay_path": None,
               "duration_ms": 100.0, "team0_rejections": 1, "team1_rejections": 2}
              for f in fixtures
          ],
      }
      path = tmp_path / "manifest.json"
      save_manifest(manifest, path)

      args = argparse.Namespace(manifest=str(path))
      _cmd_report(args)
      out = capsys.readouterr().out

      assert "Campaign:" in out
      assert "Standings" in out
      assert "Head-to-head" in out
      assert "Operational" in out
      assert "Winner claim" in out
  ```

- [ ] **Step 2: Run test to confirm it fails**

  Run: `PYTHONPATH=src pytest tests/test_campaign_report.py::test_cmd_report_sections -v`
  Expected: FAIL (sections missing from current report output)

- [ ] **Step 3: Extend `_cmd_report` in `cli_campaign.py`**

  Add imports: `head_to_head_matrix`, `operational_summary`, `winner_claim`.

  Replace the existing report print block with the five-section format above.
  Use `manifest["fixtures"]` for `operational_summary`.
  Pass `bootstrap_seed=manifest["seed_pool_start"]` to `winner_claim`.
  Completeness line: count `results` where `status == "complete"` vs `len(fixtures)`.
  Show `[FINAL]` only when complete == total and 0 failures; else `[PROVISIONAL]`.
  Head-to-head: iterate `sorted(entrant_ids)` for rows and columns; format as `W-D-L GF:GA`.
  Winner claim: show winner_id or `INCONCLUSIVE`; show `PROVISIONAL (N failures)` if not final.

- [ ] **Step 4: Run test to confirm it passes**

  Run: `PYTHONPATH=src pytest tests/test_campaign_report.py::test_cmd_report_sections -v`
  Expected: PASS

- [ ] **Step 5: Full suite + quality checks**

  Run: `PYTHONPATH=src pytest --ignore=tests/test_viewer.py --ignore=tests/test_wire.py --ignore=tests/test_process_agent.py -q`
  Expected: all pass

  Run: `ruff check src/ tests/ && mypy src/football_lab/`
  Expected: clean

- [ ] **Step 6: Commit**

  ```bash
  git add src/football_lab/cli_campaign.py tests/test_campaign_report.py
  git commit -m "feat(M4): extend report with head-to-head, winner claim, operational summary"
  ```

---

### Task 4: Benchmark execution and final report

**Files:**
- New: `local/benchmark/primary.json` (git-ignored — under `local/`)
- New: `local/benchmark/replays/` (git-ignored)

**Interfaces:**
- Consumes: CLI from Tasks 1–3, all five entrants from M3

- [ ] **Step 1: Initialise the scored campaign**

  Run:
  ```bash
  mkdir -p local/benchmark
  PYTHONPATH=src python -m football_lab campaign init \
    --entrants initial_idea_v1,redesign_1_v1,redesign_2_v1,redesign_3_v1,redesign_4_v1 \
    --blocks 100 \
    --seed-pool-start 10000 \
    --track primary \
    --output local/benchmark/primary.json
  ```

  Expected: `campaign init: 2000 fixtures → local/benchmark/primary.json`

- [ ] **Step 2: Run all fixtures**

  Run:
  ```bash
  PYTHONPATH=src python -m football_lab campaign run \
    --manifest local/benchmark/primary.json \
    --replays local/benchmark/replays
  ```

  Expected: `campaign run done: N complete, M failed` where N + M = 2000.
  Any failures → note them; the report will show `[PROVISIONAL]`.

- [ ] **Step 3: Generate and review the report**

  Run:
  ```bash
  PYTHONPATH=src python -m football_lab campaign report \
    --manifest local/benchmark/primary.json
  ```

  Expected: five-section report showing completeness, standings, head-to-head, operational
  summary and winner claim. Check that the completeness line reflects actual fixture count.

- [ ] **Step 4: Verify source preservation**

  Run:
  ```bash
  git diff HEAD -- ../initial_idea/ ../redesign_1/ ../redesign_2/ ../redesign_3/ ../redesign_4/
  ```

  Expected: no output (no changes to reference directories).

- [ ] **Step 5: Full suite one last time**

  Run: `PYTHONPATH=src pytest --ignore=tests/test_viewer.py --ignore=tests/test_wire.py --ignore=tests/test_process_agent.py -q`
  Expected: all pass

  Run: `ruff check src/ tests/ && mypy src/football_lab/`
  Expected: clean

- [ ] **Step 6: Commit**

  ```bash
  git add docs/superpowers/plans/2026-10-22-local-football-m4.md
  git commit -m "feat(M4): complete scored benchmark and extended report"
  ```

---

## Exit gate

- [ ] `local/benchmark/primary.json` exists with `2000` fixtures
- [ ] Report shows `[FINAL]` (all complete) or `[PROVISIONAL]` with explicit failure count
- [ ] Winner claim present: named winner with positive 99.5% CI lower bounds, or `INCONCLUSIVE`
- [ ] `ruff check src/ tests/` clean
- [ ] `mypy src/football_lab/` clean
- [ ] Full test suite passes
- [ ] `git diff HEAD -- ../initial_idea/ ../redesign_1/ ../redesign_2/ ../redesign_3/ ../redesign_4/` prints nothing
