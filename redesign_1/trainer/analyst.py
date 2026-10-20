"""Optional LLM analyst: reads the match report like an assistant coach and proposes
small knob changes. Never on the tick path; every output is validated and clamped.
Without ANALYST_MODEL_ID (or on any failure) it returns no proposals.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Callable, Optional

from agent.playbook import KNOBS, Playbook

log = logging.getLogger("afc.analyst")

Llm = Callable[[str], str]

PROMPT = """You are the assistant coach of a 5-a-side robot football team. The team plays
with a deterministic playbook of numeric knobs. Read the match report and propose at most
3 small knob changes that would fix the biggest problems. Each delta must be within
+/- the knob's step. Prefer no change over a guess.

Knobs (name: current [min..max] step - meaning):
{knobs}

Match report:
{report}

Reply with ONLY this JSON:
{{"proposals":[{{"knob":"<name>","delta":<number>,"reason":"<short>"}}],"summary":"<3 sentences of coach's notes>"}}"""


def build_prompt(report: dict, pb: Playbook) -> str:
    knobs = "\n".join(f"- {k}: {pb.get(k)} [{v.lo}..{v.hi}] step {v.step} - {v.doc}" for k, v in KNOBS.items())
    brief = {k: report.get(k) for k in ("kpis", "lessons_fired", "opponent", "scout_nudges")}
    return PROMPT.format(knobs=knobs, report=json.dumps(brief, indent=1, default=str)[:6000])


def parse(text: str) -> tuple[list[dict], str]:
    """Strict-ish parse: first JSON object in the text; invalid items are dropped."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return [], ""
    try:
        obj = json.loads(m.group(0))
    except ValueError:
        return [], ""
    if not isinstance(obj, dict):
        return [], ""
    out, seen = [], set()
    for p in obj.get("proposals") or []:
        if not isinstance(p, dict):
            continue
        k, d = p.get("knob"), p.get("delta")
        if k not in KNOBS or k in seen or isinstance(d, bool) or not isinstance(d, (int, float)) or d == 0:
            continue
        step = KNOBS[k].step
        d = max(-step, min(step, float(d)))
        out.append({"knob": k, "delta": round(d, 4), "reason": str(p.get("reason", ""))[:200]})
        seen.add(k)
        if len(out) == 3:
            break
    summary = obj.get("summary")
    return out, str(summary)[:1500] if isinstance(summary, str) else ""


def propose(report: dict, pb: Playbook, llm: Optional[Llm] = None) -> tuple[list[dict], str]:
    llm = llm or bedrock_llm()
    if llm is None:
        return [], ""
    try:
        return parse(llm(build_prompt(report, pb)))
    except Exception as e:  # noqa: BLE001
        log.warning("analyst failed: %r", e)
        return [], ""


def bedrock_llm(model_id: Optional[str] = None) -> Optional[Llm]:
    model_id = model_id or os.environ.get("ANALYST_MODEL_ID")
    if not model_id:
        return None

    def call(prompt: str) -> str:
        import boto3

        r = boto3.client("bedrock-runtime").converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 800, "temperature": 0.2},
        )
        return "".join(c.get("text", "") for c in r["output"]["message"]["content"])

    return call
