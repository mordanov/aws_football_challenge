"""Directory hashing to prove frozen opponents/incumbent are unchanged."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Mapping


def hash_tree(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
            h.update(str(p.relative_to(root)).encode())
            h.update(b"\0")
            h.update(p.read_bytes())
            h.update(b"\0")
    return h.hexdigest()


def verify_unchanged(dirs: Mapping[str, Path], recorded: Mapping[str, str]) -> list[str]:
    return [
        f"{name} changed since experiment init"
        for name, d in dirs.items()
        if hash_tree(d) != recorded[name]
    ]
