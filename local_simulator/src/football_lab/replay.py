import json
import os
import tempfile
from pathlib import Path

from .contracts import Replay


class JsonReplayStore:
    def save(self, replay: Replay, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            try:
                json.dump(replay, handle, allow_nan=False, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
                # shortcut: hard-link atomic publish; macOS only, same-filesystem required
                os.link(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
