"""Minimal .env loader for the assistant (standard library only).

Reads ``inav2/.env`` (or the file named by ``INA_ENV_FILE``) once and puts its
``KEY=VALUE`` pairs into ``os.environ``. Variables that are already set in the
real environment win, so ``export LLM_MODEL=...`` overrides the file. Lines
starting with ``#`` and blank lines are ignored; values may be quoted.

Owner: Khushi (assistant).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

DEFAULT_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"

_loaded = False


def parse_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key:
            values[key] = value
    return values


def load_env_file(path: Optional[str] = None, force: bool = False) -> Optional[Path]:
    """Load the .env file once; returns the path loaded, or None if there is none."""
    global _loaded
    if _loaded and not force:
        return None
    _loaded = True
    env_path = Path(path or os.environ.get("INA_ENV_FILE") or DEFAULT_ENV_FILE)
    if not env_path.is_file():
        return None
    for key, value in parse_env_file(env_path).items():
        os.environ.setdefault(key, value)
    return env_path
