"""Optional LLM tie-breaker (Strands Agents + Amazon Bedrock).

The model never writes a command. It only picks an index among candidates the
rules engine already validated, so a bad generation costs nothing, and the
output is one tiny integer, which keeps latency inside the ~1 s per-tick limit.

Hard timeout: if the model has not answered in LLM_BUDGET_S the rules' top
candidate runs instead. Strands is imported lazily so everything else works
(and is testable) without it installed.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Optional

from .model import Candidate, World

BUDGET_S = float(os.getenv("LLM_BUDGET_S", "0.55"))
MODEL_ID = os.getenv("LLM_MODEL_ID", "us.amazon.nova-micro-v1:0")  # fastest; swap per role
_pool = ThreadPoolExecutor(max_workers=2)
_agent = None

SYSTEM = (
    "You are one player in a 5-a-side football match. You are given a numbered list "
    "of legal options for the ball carrier. Choose the single best option index. "
    "Prefer: a shot with a clear lane > a pass that reaches an unmarked forward in space "
    "> carrying into space > a safe release. Never choose an option that gives the ball "
    "away cheaply. Reply with the structured result only."
)


def _build_agent():
    from pydantic import BaseModel, Field
    from strands import Agent
    from strands.models.bedrock import BedrockModel

    class Choice(BaseModel):
        index: int = Field(..., description="Index of the chosen option")
        rationale: str = Field("", description="<= 12 words, for the replay log")

    agent = Agent(
        model=BedrockModel(model_id=MODEL_ID, temperature=0.1, max_tokens=60),
        system_prompt=SYSTEM,
        callback_handler=None,
    )
    return agent, Choice


def _summary(w: World, cands: list[Candidate]) -> str:
    me = w.me
    lines = [
        f"score {w.score_us}-{w.score_them}, t={w.clock:.0f}/{w.match_len:.0f}s, "
        f"me=#{me.id} at ({me.x:.0f},{me.y:.0f}), attacking {'+x' if w.s > 0 else '-x'}",
        f"coach: {w.coach or 'none'}",
        "options:",
    ]
    lines += [f"{i}: {c.why} (rule score {c.score:.0f})" for i, c in enumerate(cands)]
    return "\n".join(lines)


def choose(w: World, cands: list[Candidate]) -> Optional[int]:
    global _agent
    if os.getenv("LLM_ENABLED", "0") != "1":
        return None
    if _agent is None:
        _agent = _build_agent()
    agent, Choice = _agent
    agent.messages.clear()  # stateless: history only inflates prefill latency

    def call() -> int:
        return int(agent.structured_output(Choice, _summary(w, cands)).index)

    fut = _pool.submit(call)
    try:
        return fut.result(timeout=BUDGET_S)
    except (FuturesTimeout, Exception):
        return None
