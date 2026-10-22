"""Optional LangChain advisor (`pip install -e '.[llm]'`); never imported by the runtime."""

from .core import Advisor, AdvisorError, ArchetypeAdvice, SettingsAdvice

__all__ = ["Advisor", "AdvisorError", "ArchetypeAdvice", "SettingsAdvice"]
