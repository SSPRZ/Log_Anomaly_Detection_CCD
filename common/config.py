"""Load config/platform.yaml with LAP__SECTION__KEY environment overrides."""
from __future__ import annotations

import copy
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "config" / "platform.yaml"


def _coerce(value: str) -> Any:
    low = value.lower()
    if low in ("true", "false"):
        return low == "true"
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            pass
    return value


def _apply_env(cfg: dict, environ: dict[str, str]) -> dict:
    for name, value in environ.items():
        if not name.startswith("LAP__"):
            continue
        path = [p.lower() for p in name[5:].split("__") if p]
        node = cfg
        for part in path[:-1]:
            node = node.setdefault(part, {})
        node[path[-1]] = _coerce(value)
    return cfg


def load_config(path: str | os.PathLike | None = None, overrides: dict | None = None) -> dict:
    path = Path(path or os.environ.get("LAP_CONFIG", DEFAULT_PATH))
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    cfg = _apply_env(cfg, dict(os.environ))
    if overrides:
        cfg = deep_merge(cfg, overrides)
    return cfg


def deep_merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


@lru_cache(maxsize=1)
def get_config() -> dict:
    return load_config()


def resolve_path(p: str) -> Path:
    """Paths in the config are relative to the repository root."""
    path = Path(p)
    return path if path.is_absolute() else ROOT / path
