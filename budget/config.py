"""Appearance and currency settings — a plain JSON file next to the app.

The accounts themselves live in data/accounts.json (see store.py).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = APP_DIR / "config.json"
DEFAULT_DATA_PATH = APP_DIR / "data" / "accounts.json"

DOCKS = ("right", "left", "none")
VALIGNS = ("top", "center", "bottom")

DEFAULTS = {
    "dock": "left",            # "right" | "left" | "none" (free floating)
    "valign": "top",           # where a docked widget sits until you drag it
    "margin": 12,
    "title": "Budget",         # heading; blank hides it
    "pie_size": 190,           # donut diameter in pixels
    "legend": True,            # list of accounts under the pie
    "show_change": True,       # "▲ €320 since 13 Sep" under the total
    "change_days": 30,
    "private": False,          # amounts masked as ••••
    "currency_symbol": "€",
    "symbol_after": False,     # True -> "12,450 €"
    "thousands_separator": ",",
    "decimal_mark": ".",
    "decimals": 0,             # on the widget; the editor always shows cents
    "font_size": 10,
    "opacity": 0.93,
    "window_layer": "normal",  # "top" | "normal" | "bottom"
    "click_through": False,
    "data_path": "",
    "position": {"x": None, "y": None},
}


def load() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    if CONFIG_PATH.exists():
        try:
            user = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(user, dict):
                cfg.update({k: v for k, v in user.items() if k in DEFAULTS})
        except Exception:
            pass
    if cfg["dock"] not in DOCKS:
        cfg["dock"] = "left"
    if cfg["valign"] not in VALIGNS:
        cfg["valign"] = "top"
    if cfg["window_layer"] not in ("top", "normal", "bottom"):
        cfg["window_layer"] = "normal"
    if not isinstance(cfg.get("position"), dict):
        cfg["position"] = {"x": None, "y": None}
    for key, lo, hi in (("pie_size", 100, 600), ("decimals", 0, 2),
                        ("change_days", 1, 3650), ("margin", 0, 200)):
        try:
            cfg[key] = max(lo, min(hi, int(cfg[key])))
        except Exception:
            cfg[key] = DEFAULTS[key]
    return cfg


def save(cfg: dict) -> None:
    try:
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, CONFIG_PATH)
    except Exception:
        pass


def data_path(cfg: dict) -> Path:
    p = (cfg.get("data_path") or "").strip()
    return Path(p) if p else DEFAULT_DATA_PATH
