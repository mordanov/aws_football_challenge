"""Off-tick LangChain advisor. Produces proposals only; the runtime never imports this."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..model import Profile
from ..profile import parse_profile

SYSTEM = (
    "You advise a 5-a-side football team between matches. Reply with one JSON object "
    "and nothing else. Never invent fields. "
)
PICK = (
    "Pick the strategy archetype with the strongest evidence against the opponent. "
    'Schema: {{"archetype": one of {names}, "coach_policy": "none" | "rules", '
    '"rationale": string}}.'
)
REVIEW = (
    "Propose small adjustments to the current profile after this match report. "
    'Schema: {"settings": <all current settings keys>, "style": <all current style keys>, '
    '"rationale": string}. Stay within the bounds you are given.'
)


class AdvisorError(RuntimeError):
    """The model failed or returned something that is not a valid proposal."""


class ArchetypeAdvice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    archetype: str
    coach_policy: Literal["none", "rules"]
    rationale: str = Field(min_length=1, max_length=2000)


class SettingsAdvice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    settings: dict[str, float]
    style: dict[str, Any]
    rationale: str = Field(min_length=1, max_length=2000)


def _text(content: Any) -> str:
    if isinstance(content, list):
        content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
    text = str(content).strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()


class Advisor:
    def __init__(self, llm: Any, archetypes: dict[str, Profile]):
        self.llm = llm
        self.archetypes = archetypes

    def _ask(self, schema: type[BaseModel], task: str, payload: dict) -> Any:
        messages = [
            SystemMessage(SYSTEM + task),
            HumanMessage(json.dumps(payload, sort_keys=True, default=str)),
        ]
        try:
            reply = self.llm.invoke(messages)
        except Exception as exc:  # provider, network and throttling errors alike
            raise AdvisorError(f"model call failed: {exc}") from exc
        try:
            return schema.model_validate_json(_text(reply.content))
        except ValidationError as exc:
            raise AdvisorError(f"invalid model output: {exc}") from exc

    @staticmethod
    def _checked(data: dict) -> Profile:
        try:
            return parse_profile(data)
        except (ValueError, TypeError, KeyError) as exc:
            raise AdvisorError(f"proposal rejected: {exc}") from exc

    def pick_archetype(self, matrix: dict, opponent_id: str) -> tuple[ArchetypeAdvice, Profile]:
        task = PICK.format(names=json.dumps(sorted(self.archetypes)))
        advice = self._ask(ArchetypeAdvice, task, {"opponent_id": opponent_id, "matrix": matrix})
        if advice.archetype not in self.archetypes:
            raise AdvisorError(f"unknown archetype: {advice.archetype}")
        base = self.archetypes[advice.archetype]
        profile = replace(
            base,
            version=f"advice-{advice.archetype}-{advice.coach_policy}",
            coach_policy=advice.coach_policy,
        )
        return advice, self._checked(profile.to_dict())

    def review_match(self, report: dict, current: Profile) -> tuple[SettingsAdvice, Profile]:
        advice = self._ask(SettingsAdvice, REVIEW, {"report": report, "current": current.to_dict()})
        data = {
            **current.to_dict(),
            "version": f"{current.version}-review",
            "settings": advice.settings,
            "style": advice.style,
        }
        return advice, self._checked(data)
