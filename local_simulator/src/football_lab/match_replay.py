"""Match replay v2 save / load."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from football_lab.match_contracts import MatchReplay


class JsonMatchReplayStore:
    """Atomic write, no-overwrite replay store."""

    def save(self, replay: MatchReplay, path: Path) -> None:
        path = Path(path)
        if path.exists():
            raise OSError(f"replay already exists: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(replay, allow_nan=False)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            os.write(fd, data.encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.link(tmp, path)
        finally:
            os.unlink(tmp)


def load_match_replay(path: Path) -> MatchReplay:
    data: Any = json.loads(Path(path).read_text())
    if data.get("schema_version") != 2:
        raise ValueError(f"expected schema_version 2, got {data.get('schema_version')}")
    if data.get("kind") != "match-replay":
        raise ValueError(f"expected kind 'match-replay', got {data.get('kind')}")
    return data  # type: ignore[return-value]
