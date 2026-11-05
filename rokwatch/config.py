from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_PATH = "config.yaml"


def load(path: str = DEFAULT_PATH) -> dict:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"{path} not found. Copy config.example.yaml to {path} first.")
    return yaml.safe_load(p.read_text())


def save_fields(updates: dict, path: str = DEFAULT_PATH) -> None:
    """Update a few top-level / nested values, keeping the rest of the file."""
    cfg = load(path)
    for key, value in updates.items():
        parts = key.split(".")
        node = cfg
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    Path(path).write_text(yaml.safe_dump(cfg, sort_keys=False))
