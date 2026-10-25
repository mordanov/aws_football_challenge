"""Optional LangChain advisor (`pip install -e '.[llm]'`); never imported by the runtime."""

import os
import tempfile
from pathlib import Path

try:
    from .core import Advisor, AdvisorError, ArchetypeAdvice, SettingsAdvice
except ImportError:
    pass


def write_atomic(path: Path, text: str) -> None:
    """Write text to path atomically via a temp file in the same directory."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


__all__ = ["Advisor", "AdvisorError", "ArchetypeAdvice", "SettingsAdvice", "write_atomic"]
