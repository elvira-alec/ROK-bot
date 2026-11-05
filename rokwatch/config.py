"""Settings: config.yaml (yours to edit) + setup.yaml (written by the setup step)."""

from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_PATH = "config.yaml"


def _merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in extra.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def setup_path(cfg_path: str = DEFAULT_PATH) -> Path:
    return Path(cfg_path).with_name("setup.yaml")


def reference_path(cfg_path: str = DEFAULT_PATH) -> str:
    """The setup screenshot lives next to the config file."""
    return str(Path(cfg_path).with_name("calibration.png"))


def load(path: str = DEFAULT_PATH) -> dict:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"{path} not found. Copy config.example.yaml to {path} first.")
    cfg = yaml.safe_load(p.read_text()) or {}
    sp = setup_path(path)
    if sp.exists():
        cfg = _merge(cfg, yaml.safe_load(sp.read_text()) or {})
    return cfg


def save_fields(updates: dict, path: str = DEFAULT_PATH) -> None:
    """Save dotted keys (e.g. 'enemy_watch.enemy_city') into setup.yaml."""
    sp = setup_path(path)
    data = (yaml.safe_load(sp.read_text()) or {}) if sp.exists() else {}
    for key, value in updates.items():
        parts = key.split(".")
        node = data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    sp.write_text("# Written by the setup step. Delete this file to redo setup.\n"
                  + yaml.safe_dump(data, sort_keys=False))
