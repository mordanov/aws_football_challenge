"""In-match LLM coach using Nova Pro. Run as a standalone process alongside a live match."""
from __future__ import annotations

import json
import logging
import time
from collections import Counter
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MODEL_ID = "us.amazon.nova-pro-v1:0"
S3_PREFIX = "final-agent-learning/live-coach"

CW_GROUPS: dict[int, str] = {
    0: "/aws/bedrock-agentcore/runtimes/Redesign4_default_r4_gk-nxusigFggA-DEFAULT",
    1: "/aws/bedrock-agentcore/runtimes/Redesign4_default_r4_def-t1Kh1c7eeZ-DEFAULT",
    2: "/aws/bedrock-agentcore/runtimes/Redesign4_default_r4_mid-Nq5Hfb67re-DEFAULT",
    3: "/aws/bedrock-agentcore/runtimes/Redesign4_default_r4_fwd1-7vRt5lCgqV-DEFAULT",
    4: "/aws/bedrock-agentcore/runtimes/Redesign4_default_r4_fwd2-syVGGx7Rzk-DEFAULT",
}

PARAM_RANGES: dict[str, tuple[float, float]] = {
    "rush_s":       (0, 20),
    "rush_enabled": (0, 1),
    "press_radius": (15, 28),
    "desperate_s":  (20, 50),
    "support_width": (5, 20),
}

DEFAULTS: dict[str, float] = {
    "rush_s":        15,
    "rush_enabled":  1,
    "press_radius":  20,
    "desperate_s":   30,
    "support_width": 12,
}

_SYSTEM = """You are an in-match football tactics coach for an AI agent team.
You receive the current match state and current strategy parameters.
Suggest parameter adjustments to improve performance.

Controllable parameters and valid ranges:
- rush_s (0-20): seconds. Rush tactic active when clock < rush_s at 0:0 score. Reduce to limit risk.
- rush_enabled (0-1): 0 disables rush entirely.
- press_radius (15-28): metres. How close an opponent must be to trigger pressing.
- desperate_s (20-50): seconds remaining when losing, enabling all-out attack.
- support_width (5-20): metres. How wide support players spread laterally.

Respond ONLY with valid JSON. No prose before or after the JSON object.
{"changes": {"param": value, ...}, "reason": "one sentence"}

Only include params you want to change. Empty changes {} means keep current settings."""


@dataclass
class MatchState:
    clock: float = 0.0
    duration: float = 120.0
    our_score: int = 0
    opp_score: int = 0
    our_possession_pct: int = 0
    rush_events: int = 0
    commands: dict[str, int] = field(default_factory=dict)
    decisions_seen: int = 0


def _fetch_stream(cw, group: str, match_id: str, team_side: str, pos: int, date: str) -> list[dict]:
    prefix = f"{date}/[runtime-logs-{match_id}-{team_side}-pos{pos}]"
    try:
        r = cw.describe_log_streams(logGroupName=group, logStreamNamePrefix=prefix, limit=1)
        streams = r.get("logStreams", [])
        if not streams:
            return []
        r2 = cw.get_log_events(
            logGroupName=group,
            logStreamName=streams[0]["logStreamName"],
            limit=300,
            startFromHead=False,
        )
        return r2.get("events", [])
    except Exception as exc:
        logger.debug("CW fetch pos%d: %s", pos, exc)
        return []


def build_state(cw, match_id: str, team_side: str, date: str) -> MatchState:
    team_idx = 1 if team_side == "team1" else 0
    state = MatchState()
    all_decs: list[dict] = []

    for pos, group in CW_GROUPS.items():
        for ev in _fetch_stream(cw, group, match_id, team_side, pos, date):
            try:
                msg = json.loads(ev["message"])
                if msg.get("type") in ("decision", "error"):
                    all_decs.append(msg)
            except Exception:
                pass

    if not all_decs:
        return state

    all_decs.sort(key=lambda d: (d.get("snapshot", {}).get("clock", 0), d.get("tick", 0)))
    snap = all_decs[-1].get("snapshot", {})
    score = snap.get("score", [0, 0])
    state.clock = snap.get("clock", 0.0)
    state.duration = snap.get("duration", 120.0)
    state.our_score = score[team_idx] if len(score) > team_idx else 0
    state.opp_score = score[1 - team_idx] if len(score) > (1 - team_idx) else 0

    owned = [d for d in all_decs if (d.get("snapshot", {}).get("owner") or [None])[0] is not None]
    if owned:
        ours = sum(1 for d in owned if (d.get("snapshot", {}).get("owner") or [None])[0] == team_idx)
        state.our_possession_pct = round(100 * ours / len(owned))

    state.commands = dict(
        Counter(d.get("command", {}).get("commandType") for d in all_decs if d.get("type") == "decision").most_common(8)
    )
    state.rush_events = sum(1 for d in all_decs if d.get("rush") == 1)
    state.decisions_seen = len(all_decs)
    return state


def _state_prompt(state: MatchState, current: dict[str, float]) -> str:
    remaining = state.duration - state.clock
    result = (
        f"Winning {state.our_score}:{state.opp_score}" if state.our_score > state.opp_score
        else f"Losing {state.our_score}:{state.opp_score}" if state.opp_score > state.our_score
        else f"Drawing {state.our_score}:{state.opp_score}"
    )
    top = ", ".join(f"{c}:{n}" for c, n in list(state.commands.items())[:5])
    params = "\n".join(f"  {k}: {v}" for k, v in sorted(current.items()))
    return (
        f"Clock: {state.clock:.0f}s / {state.duration:.0f}s (remaining: {remaining:.0f}s)\n"
        f"Score: {result}\n"
        f"Our possession: {state.our_possession_pct}%\n"
        f"Rush events so far: {state.rush_events}\n"
        f"Top commands: {top}\n"
        f"Decisions seen: {state.decisions_seen}\n"
        f"\nCurrent parameters:\n{params}"
    )


def call_coach(bedrock, state: MatchState, current: dict[str, float]) -> dict:
    try:
        resp = bedrock.converse(
            modelId=MODEL_ID,
            system=[{"text": _SYSTEM}],
            messages=[{"role": "user", "content": [{"text": _state_prompt(state, current)}]}],
            inferenceConfig={"maxTokens": 256, "temperature": 0.0},
        )
        raw = resp.get("output", {}).get("message", {}).get("content", [])
        text = raw[0].get("text", "") if raw else ""
        start, end = text.find("{"), text.rfind("}") + 1
        if start < 0 or end <= start:
            logger.warning("No JSON in coach response: %r", text[:200])
            return {"changes": {}, "reason": "parse error"}
        result = json.loads(text[start:end])
        changes = {}
        for k, v in result.get("changes", {}).items():
            if k in PARAM_RANGES:
                lo, hi = PARAM_RANGES[k]
                changes[k] = max(lo, min(hi, float(v)))
        result["changes"] = changes
        return result
    except Exception as exc:
        logger.error("Coach call failed: %s", exc)
        return {"changes": {}, "reason": f"error: {exc}"}


def write_advice(s3, bucket: str, match_id: str, advice: dict) -> None:
    key = f"{S3_PREFIX}/{match_id}.json"
    body = json.dumps({**advice, "match_id": match_id, "ts": time.time()}).encode()
    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")
    logger.info("Advice written: %s → %s", key, advice.get("reason", ""))


class CoachLoop:
    """Runs coaching loop for one match. Start manually when match begins."""

    def __init__(
        self,
        match_id: str,
        team_side: str,
        bucket: str,
        date: str = "",
        region: str = "us-east-1",
    ):
        import datetime
        import boto3  # type: ignore[import]
        from botocore.config import Config  # type: ignore[import]

        self.match_id = match_id
        self.team_side = team_side
        self.bucket = bucket
        self.date = date or datetime.date.today().strftime("%Y/%m/%d")
        cfg = Config(read_timeout=10, connect_timeout=5, retries={"max_attempts": 2})
        self._cw = boto3.client("logs", region_name=region, config=cfg)
        self._s3 = boto3.client("s3", region_name=region, config=cfg)
        self._bedrock = boto3.client("bedrock-runtime", region_name=region, config=cfg)
        self._current: dict[str, float] = dict(DEFAULTS)

    def run_once(self) -> dict:
        state = build_state(self._cw, self.match_id, self.team_side, self.date)
        if state.decisions_seen == 0:
            logger.info("No decisions yet for %s", self.match_id[:8])
            return {}
        advice = call_coach(self._bedrock, state, self._current)
        if advice.get("changes"):
            self._current.update(advice["changes"])
            write_advice(self._s3, self.bucket, self.match_id, advice)
        return advice

    def run(self, interval_s: float = 25.0, max_iterations: int = 300) -> None:
        logger.info("CoachLoop: match=%s %s", self.match_id[:8], self.team_side)
        for i in range(max_iterations):
            t0 = time.monotonic()
            try:
                advice = self.run_once()
                if advice.get("changes"):
                    logger.info("Tick %d: %s → %s", i + 1, advice.get("reason", ""), advice["changes"])
            except Exception as exc:
                logger.error("Tick %d: %s", i + 1, exc)
            time.sleep(max(0.0, interval_s - (time.monotonic() - t0)))
