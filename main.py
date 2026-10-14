"""Entry point: python main.py  (or run.bat)

Flags:
  --list                 print every account and the total, then exit
  --set NAME AMOUNT      set an account's amount
  --add NAME AMOUNT      add to an account (negative to subtract)
  --selftest             check the maths and the data file, then exit

NAME matches an account's name (case-insensitive, a unique prefix is enough).
A running widget picks the change up within a couple of seconds.
"""
from __future__ import annotations

import sys


def _load():
    from budget import config as cfg_mod
    from budget.store import Store
    cfg = cfg_mod.load()
    return cfg, Store(cfg_mod.data_path(cfg))


def list_accounts() -> int:
    from budget import model
    cfg, store = _load()
    net, assets, debts = model.totals(store.accounts)
    print(f"Budget Pie  —  {store.path}\n" + "-" * 56)
    for a in store.accounts:
        share = (f"{a['amount'] / assets * 100:5.1f}%" if a["amount"] > 0 and assets
                 else "  debt" if a["amount"] < 0 else "")
        print(f"{a['name'][:28]:28} {model.fmt_money(a['amount'], cfg, 2):>18} {share:>7}")
    print("-" * 56)
    print(f"{'Net' if debts else 'Total':28} {model.fmt_money(net, cfg, 2):>18}")
    ch = model.change(store.history, net)
    if ch:
        print(f"{'Change since ' + ch[1].isoformat():28} "
              f"{model.fmt_money(ch[0], cfg, 2, sign=True):>18}")
    return 0


def change_account(args: list[str], mode: str) -> int:
    from budget import model
    if len(args) < 2:
        print(f"usage: main.py --{mode} NAME AMOUNT")
        return 2
    cfg, store = _load()
    name, raw = " ".join(args[:-1]), args[-1].replace(" ", "").replace("\u00a0", "")
    if cfg.get("decimal_mark") == ",":
        raw = raw.replace(".", "").replace(",", ".")    # 8.250,50 -> 8250.50
    else:
        raw = raw.replace(",", "")                      # 8,250.50 -> 8250.50
    try:
        value = float(raw)
    except ValueError:
        print(f"Not a number: {args[-1]}")
        return 2
    a = store.find(name)
    if a is None:
        print(f"No account matches “{name}”. Try --list.")
        return 1
    model.set_amount(a, a["amount"] + value if mode == "add" else value)
    if not store.save():
        print("Could not write the data file.")
        return 1
    net = model.totals(store.accounts)[0]
    print(f"{a['name']}: {model.fmt_money(a['amount'], cfg, 2)}   "
          f"(total {model.fmt_money(net, cfg, 2)})")
    return 0


def selftest() -> int:
    import tempfile
    from datetime import date
    from pathlib import Path

    from budget import model
    from budget.store import Store

    ok = True

    def check(label, cond, got=""):
        nonlocal ok
        ok &= bool(cond)
        print(f"{'PASS' if cond else 'FAIL'}  {label}" + (f"   -> {got}" if got else ""))

    print("Budget Pie — self-test\n" + "-" * 50)
    check("format €", model.fmt_money(12450) == "€12,450", model.fmt_money(12450))
    eu = {"symbol_after": True, "thousands_separator": " ", "decimal_mark": ","}
    got = model.fmt_money(12450.5, eu, 2)
    check("format 12 450,50 €", got == "12 450,50 €", got)
    check("format negative", model.fmt_money(-1200) == "−€1,200")

    accs = [model.new_account("A", 6000), model.new_account("B", 3000),
            model.new_account("C", 1000), model.new_account("Card", -500)]
    net, assets, debts = model.totals(accs)
    check("totals", (net, assets, debts) == (9500, 10000, 500), f"{net} {assets} {debts}")
    sl = model.slices(accs)
    check("slices skip debts, shares add to 1",
          len(sl) == 3 and abs(sum(f for _, f in sl) - 1) < 1e-9,
          ", ".join(f"{a['name']} {f:.0%}" for a, f in sl))
    check("no slices when nothing positive", model.slices([model.new_account("x", -5)]) == [])

    t = date(2026, 10, 13)
    h = [["2026-09-01", 20000], ["2026-09-12", 21000], ["2026-10-12", 22000]]
    ch = model.change(h, 23000, t)
    check("change vs a month ago", ch == (2000, date(2026, 9, 12)), str(ch))
    check("no change on day one", model.change([["2026-10-13", 1]], 5, t) is None)
    h2 = model.record(h, 23000, t)
    h2 = model.record(h2, 23100, t)
    check("one history row per day", h2[-1] == ["2026-10-13", 23100] and len(h2) == 4,
          str(h2[-1]))

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "accounts.json"
        s = Store(path)
        check("fresh store seeds examples, no history yet",
              len(s.accounts) == 4 and s.created_fresh and not s.history)
        model.set_amount(s.find("cash"), 400)
        check("save records today's total", s.save() and len(s.history) == 1,
              str(s.history))
        again = Store(path)
        check("round-trip", again.find("cash")["amount"] == 400 and len(again.history) == 1)
        path.write_text("{broken", encoding="utf-8")
        check("falls back to .bak when the file is corrupt",
              len(Store(path, seed=False).accounts) == 4)

    print("\nresult:", "ALL PASS" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:  # an old code page shouldn't crash --list over a symbol
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    argv = sys.argv[1:]
    if "--selftest" in argv:
        return selftest()
    if "--list" in argv:
        return list_accounts()
    for mode in ("set", "add"):
        if f"--{mode}" in argv:
            return change_account(argv[argv.index(f"--{mode}") + 1:], mode)

    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("PySide6 is not installed. Run:\n"
              "    pip install -r requirements.txt", file=sys.stderr)
        return 1

    from PySide6.QtNetwork import QLocalServer, QLocalSocket

    from budget import config as cfg_mod
    from budget import theme
    from budget.store import Store
    from budget.widget import BudgetWindow

    cfg = cfg_mod.load()
    app = QApplication(sys.argv)
    app.setApplicationName("Budget Pie")
    app.setQuitOnLastWindowClosed(False)
    theme.apply(app)

    # One instance per data file: two would overwrite each other's edits.
    key = "BudgetPie-" + str(cfg_mod.data_path(cfg)).lower().replace("\\", "-").replace("/", "-")
    probe = QLocalSocket()
    probe.connectToServer(key)
    if probe.waitForConnected(400):
        probe.write(b"show")
        probe.flush()
        probe.waitForBytesWritten(400)
        probe.disconnectFromServer()
        print("Budget Pie is already running — showing the existing one.")
        return 0
    QLocalServer.removeServer(key)
    server = QLocalServer()
    server.listen(key)

    store = Store(cfg_mod.data_path(cfg))
    win = BudgetWindow(cfg, store)
    win.show()

    def _second_launch():
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.disconnectFromServer()
        win.show()
        win.raise_()
        win.activateWindow()

    server.newConnection.connect(_second_launch)
    app.aboutToQuit.connect(server.close)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
