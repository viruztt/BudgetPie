"""Accounts, totals, money formatting and the history of totals. No Qt here.

An account is a plain dict (that is what lands in accounts.json):

    {"id": "a1b2c3d4", "name": "Savings", "amount": 8000, "color": "#3FC1E0",
     "note": "", "updated": "2026-10-13"}

Negative amounts (a credit card, a loan) are allowed: they are left out of the
pie, listed in red, and subtracted from the total, which then reads "NET".
"""
from __future__ import annotations

import math
import uuid
from datetime import date, timedelta

PALETTE = [
    "#3FC1E0", "#56D07A", "#E0A03F", "#7C6CE8", "#E04FB0",
    "#E0703F", "#9BD04F", "#4FA8E0", "#D2526E", "#E0C13F",
]

KEEP_HISTORY_DAYS = 1095


# --------------------------------------------------------------------------- #
# numbers
# --------------------------------------------------------------------------- #
def num(v, fallback=0.0) -> float:
    try:
        f = float(v)
        return fallback if math.isnan(f) or math.isinf(f) else f
    except Exception:
        return fallback


def fmt_money(v: float, cfg: dict | None = None, decimals: int | None = None,
              sign: bool = False) -> str:
    """€12,450 by default; symbol, its side, separator and decimals come from
    the config so '12 450,00 €' is one setting away."""
    cfg = cfg or {}
    sym = cfg.get("currency_symbol", "€")
    after = bool(cfg.get("symbol_after", False))
    sep = cfg.get("thousands_separator", ",")
    dec_mark = cfg.get("decimal_mark", ".")
    d = int(cfg.get("decimals", 0) if decimals is None else decimals)

    neg = v < 0
    body = f"{abs(v):,.{d}f}"                      # 12,450.00
    whole, _, frac = body.partition(".")
    whole = whole.replace(",", sep)
    body = whole + (dec_mark + frac if frac else "")
    out = f"{body} {sym}" if after else f"{sym}{body}"
    if neg:
        return "−" + out
    return ("+" + out) if sign and v > 0 else out


def fmt_pct(frac: float) -> str:
    p = frac * 100
    if 0 < p < 1:
        return "<1%"
    if p >= 10 or float(p).is_integer():
        return f"{p:.0f}%"
    return f"{p:.1f}%"


MASK = "••••"


# --------------------------------------------------------------------------- #
# accounts
# --------------------------------------------------------------------------- #
def new_id() -> str:
    return uuid.uuid4().hex[:8]


def new_account(name: str = "", amount: float = 0.0, color: str | None = None,
                note: str = "") -> dict:
    return {"id": new_id(), "name": name, "amount": amount,
            "color": color or PALETTE[0], "note": note,
            "updated": date.today().isoformat()}


def is_color(v) -> bool:
    return isinstance(v, str) and len(v) == 7 and v.startswith("#") and all(
        c in "0123456789abcdefABCDEF" for c in v[1:])


def normalize(a: dict) -> dict:
    out = new_account()
    out.update({k: v for k, v in a.items() if v is not None})
    out["id"] = str(out.get("id") or new_id())
    out["name"] = str(out.get("name") or "").strip()
    out["note"] = str(out.get("note") or "").strip()
    amt = num(out.get("amount"))
    out["amount"] = int(amt) if amt.is_integer() else round(amt, 2)
    if not is_color(out.get("color")):
        out["color"] = PALETTE[0]
    try:
        date.fromisoformat(str(out.get("updated"))[:10])
    except Exception:
        out["updated"] = date.today().isoformat()
    return out


def set_amount(a: dict, value: float, today: date | None = None) -> None:
    v = round(num(value), 2)
    a["amount"] = int(v) if v.is_integer() else v
    a["updated"] = (today or date.today()).isoformat()


def totals(accounts: list[dict]) -> tuple[float, float, float]:
    """(net, assets, debts) — debts as a positive number."""
    assets = sum(a["amount"] for a in accounts if a["amount"] > 0)
    debts = -sum(a["amount"] for a in accounts if a["amount"] < 0)
    return assets - debts, assets, debts


def slices(accounts: list[dict]) -> list[tuple[dict, float]]:
    """Positive accounts with their share of the assets, in list order."""
    _, assets, _ = totals(accounts)
    if assets <= 0:
        return []
    return [(a, a["amount"] / assets) for a in accounts if a["amount"] > 0]


def next_color(accounts: list[dict]) -> str:
    used = {a["color"].upper() for a in accounts}
    for c in PALETTE:
        if c.upper() not in used:
            return c
    return PALETTE[len(accounts) % len(PALETTE)]


def age_text(iso: str, today: date | None = None) -> str:
    today = today or date.today()
    try:
        d = date.fromisoformat(str(iso)[:10])
    except Exception:
        return ""
    n = (today - d).days
    if n <= 0:
        return "updated today"
    if n == 1:
        return "updated yesterday"
    if n < 60:
        return f"updated {n} days ago"
    return f"updated {d:%d %b %Y}".replace(" 0", " ")


# --------------------------------------------------------------------------- #
# history of the total
# --------------------------------------------------------------------------- #
def record(history: list, net: float, today: date | None = None) -> list:
    """One [date, net] row per day; the latest value of the day wins."""
    key = (today or date.today()).isoformat()
    rows = [r for r in history if isinstance(r, list) and len(r) == 2]
    v = round(net, 2)
    if rows and rows[-1][0] == key:
        rows[-1][1] = v
    else:
        rows.append([key, v])
    cutoff = ((today or date.today()) - timedelta(days=KEEP_HISTORY_DAYS)).isoformat()
    return [r for r in rows if r[0] >= cutoff]


def change(history: list, net: float, today: date | None = None,
           days: int = 30) -> tuple[float, date] | None:
    """Change in the total versus ~`days` ago (or the oldest row, if newer).
    None until there is at least one row from an earlier day."""
    today = today or date.today()
    target = today - timedelta(days=days)
    rows = []
    for r in history:
        try:
            d = date.fromisoformat(r[0])
        except Exception:
            continue
        if d < today:
            rows.append((d, num(r[1])))
    if not rows:
        return None
    rows.sort()
    old_enough = [r for r in rows if r[0] <= target]
    d, v = old_enough[-1] if old_enough else rows[0]
    return net - v, d


def sample_accounts() -> list[dict]:
    note = "Example amount — edit me"
    return [
        new_account("Checking account", 2400, PALETTE[0], note),
        new_account("Savings", 8000, PALETTE[1], note),
        new_account("Investments", 12500, PALETTE[3], note),
        new_account("Cash", 350, PALETTE[2], note),
    ]
