"""Accounts on disk: data/accounts.json.

Written atomically (temp file + os.replace) with one rolling .bak. Each save
also records the day's total in `history`, which drives the "since …" change
shown under the total. The widget reloads when the file changes on disk.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import model

VERSION = 1


class Store:
    def __init__(self, path: str | os.PathLike, seed: bool = True):
        self.path = Path(path)
        self.accounts: list[dict] = []
        self.history: list = []
        self._mtime = 0.0
        self.created_fresh = False
        if not self.load() and seed:
            self.accounts = model.sample_accounts()
            self.created_fresh = True
            self.save(record=False)   # example figures shouldn't start a history

    def load(self) -> bool:
        for candidate in (self.path, self.path.with_suffix(".json.bak")):
            if not candidate.exists():
                continue
            try:
                raw = json.loads(candidate.read_text(encoding="utf-8"))
            except Exception:
                continue
            items = raw.get("accounts") if isinstance(raw, dict) else raw
            if not isinstance(items, list):
                continue
            out, seen = [], set()
            for a in items:
                if not isinstance(a, dict):
                    continue
                a = model.normalize(a)
                while a["id"] in seen:
                    a["id"] = model.new_id()
                seen.add(a["id"])
                out.append(a)
            self.accounts = out
            hist = raw.get("history") if isinstance(raw, dict) else None
            self.history = [r for r in (hist or [])
                            if isinstance(r, list) and len(r) == 2 and isinstance(r[0], str)]
            self._mtime = self._disk_mtime()
            return True
        return False

    def save(self, record: bool = True) -> bool:
        if record:
            self.history = model.record(self.history, model.totals(self.accounts)[0])
        payload = json.dumps({"version": VERSION, "accounts": self.accounts,
                              "history": self.history}, indent=2, ensure_ascii=False)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                try:
                    os.replace(self.path, self.path.with_suffix(".json.bak"))
                except Exception:
                    pass
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self.path)
            self._mtime = self._disk_mtime()
            return True
        except Exception:
            return False

    def _disk_mtime(self) -> float:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return 0.0

    def changed_on_disk(self) -> bool:
        m = self._disk_mtime()
        return bool(m) and m != self._mtime

    def get(self, aid: str) -> dict | None:
        return next((a for a in self.accounts if a["id"] == aid), None)

    def find(self, text: str) -> dict | None:
        """Exact id, then case-insensitive name, then unique name prefix."""
        key = text.strip().lower()
        for a in self.accounts:
            if a["id"].lower() == key:
                return a
        named = [a for a in self.accounts if a["name"].lower() == key]
        if named:
            return named[0]
        pref = [a for a in self.accounts if a["name"].lower().startswith(key)]
        return pref[0] if len(pref) == 1 else None

    def remove(self, aid: str) -> None:
        self.accounts = [a for a in self.accounts if a["id"] != aid]

    def replace_all(self, accounts: list[dict]) -> None:
        self.accounts = [model.normalize(a) for a in accounts]
