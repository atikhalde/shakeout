"""Tiny .env loader (no external dependency)."""

from __future__ import annotations

import os


def load_env(path: str = ".env") -> None:
    """Load KEY=VALUE pairs from `path` into os.environ (won't override
    variables that are already set). Comments (#) and blank lines ignored.
    Values may be quoted with ' or "."""
    if not path or not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            os.environ.setdefault(key, value)
