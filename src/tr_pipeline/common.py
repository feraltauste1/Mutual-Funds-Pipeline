from __future__ import annotations

import hashlib
import json
import os
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ASSET_TYPES = ("stock", "mutual_fund", "etf")
LABELS = {"stock": "Acciones", "mutual_fund": "Fondos tradicionales", "etf": "ETF"}


def root_dir() -> Path:
    if os.getenv("TR_PIPELINE_ROOT"):
        return Path(os.environ["TR_PIPELINE_ROOT"]).resolve()
    for p in (Path.cwd(), *Path.cwd().parents):
        if (p / "config.toml").exists() and (p / "pyproject.toml").exists():
            return p
    raise FileNotFoundError("Ejecuta desde el repositorio o define TR_PIPELINE_ROOT.")


def config(root: Path) -> dict:
    with (root / "config.toml").open("rb") as f:
        cfg = tomllib.load(f)
    cap = cfg.get("max_assets_per_type")
    if cap is not None and (type(cap) is not int or cap < 1):
        raise ValueError("max_assets_per_type debe ser un entero positivo.")
    return cfg


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def today() -> pd.Timestamp:
    # El día actual no se utiliza: podría contener una vela aún abierta.
    return pd.Timestamp.now(tz="Europe/Madrid").normalize().tz_localize(None)


def json_write(path: Path, value):
    def clean(v):
        if isinstance(v, dict):
            return {str(k): clean(x) for k, x in v.items()}
        if isinstance(v, (tuple, list)):
            return [clean(x) for x in v]
        if isinstance(v, (np.integer, np.floating, np.bool_)):
            v = v.item()
        if isinstance(v, float) and not np.isfinite(v):
            return None
        if isinstance(v, (pd.Timestamp, datetime, Path)):
            return str(v)
        return v
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(clean(value), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    tmp.replace(path)


def json_read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def csv_write(path: Path, frame: pd.DataFrame):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    frame.to_csv(tmp, index=False, encoding="utf-8")
    tmp.replace(path)

