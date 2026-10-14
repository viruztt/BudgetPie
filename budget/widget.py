"""The floating donut: one coloured slice per account, the total in the middle."""
from __future__ import annotations

import math
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import date

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QAction, QActionGroup, QColor, QFont, QFontMetrics, QGuiApplication, QIcon,
    QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QInputDialog, QMenu, QMessageBox, QSystemTrayIcon, QToolTip,
    QWidget,
)

from . import config as cfg_mod
from . import model

BG = QColor(14, 15, 20, 225)
TEXT = QColor(238, 240, 245)
MUTED = QColor(150, 156, 170)
TRACK = QColor(255, 255, 255, 26)
UP = QColor("#56D07A")
DOWN = QColor("#E0553F")

SNAP_PX = 40
PAD = 12
HOVER_GROW = 6        # a hovered slice pushes out this far
HOLE = 0.62           # hole diameter as a share of the pie

CURRENCIES = [
    # label, symbol, symbol_after, thousands, decimal mark
    ("€12,450.00", "€", False, ",", "."),
    ("12 450,00 €", "€", True, " ", ","),
    ("12.450,00 €", "€", True, ".", ","),
    ("$12,450.00", "$", False, ",", "."),
    ("£12,450.00", "£", False, ",", "."),
    ("12 450,00 ₴", "₴", True, " ", ","),
]


@dataclass
class Item:
    kind: str                # title | eye | pie | row | empty
    rect: QRectF
    account: dict | None = None


class BudgetWindow(QWidget):
    def __init__(self, cfg: dict, store):
        super().__init__()
        self.cfg = cfg
        self.store = store
        self.items: list[Item] = []
        self.pie_rect = QRectF()
        self._spans: list[tuple[dict, float, float]] = []   # account, start, span (cw deg)
        self._hover_id: str | None = None
        self._hover_kind: str | None = None
        self._press_pos: QPoint | None = None
        self._press_kind: str | None = None
        self._drag_offset: QPoint | None = None
        self._dragged = False
        self._editor = None

        self.setWindowTitle("Budget Pie")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setWindowFlag(Qt.Tool, True)
        self._apply_flags()
        self.setWindowOpacity(float(cfg.get("opacity", 0.93)))
        self.setMouseTracking(True)

        self._tray()
        self.relayout()

        self.watch = QTimer(self)
        self.watch.timeout.connect(self._check_disk)
        self.watch.start(2000)
        self.daily = QTimer(self)       # "since …" and "updated n days ago" age
        self.daily.timeout.connect(self.update)
        self.daily.start(10 * 60_000)

        app = QGuiApplication.instance()
        app.screenAdded.connect(lambda *_: QTimer.singleShot(500, self.place))
        app.screenRemoved.connect(lambda *_: QTimer.singleShot(500, self.place))

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    def _fonts(self):
        pt = float(self.cfg.get("font_size", 10))
        base = QFont(self.font())

        def f(size, bold=False, spacing=None):
            x = QFont(base)
            x.setPointSizeF(max(6.5, size))
            x.setBold(bold)
            if spacing:
                x.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
            return x

        return {"heading": f(pt + 1.5, True), "row": f(pt), "row_b": f(pt, True),
                "pct": f(pt - 1), "label": f(pt - 2, True, 0.8), "small": f(pt - 1)}

    def money(self, v: float, decimals: int | None = None, sign: bool = False) -> str:
        if self.cfg.get("private"):
            return model.MASK
        return model.fmt_money(v, self.cfg, decimals, sign)

    # ------------------------------------------------------------------ #
    # layout
    # ------------------------------------------------------------------ #
    def relayout(self) -> None:
        fonts = self._fonts()
        fm = {k: QFontMetrics(v) for k, v in fonts.items()}
        d = int(self.cfg.get("pie_size", 190))
        legend = bool(self.cfg.get("legend", True)) and self.store.accounts
        w = max(d + 2 * HOVER_GROW + 2 * PAD, 270 if legend else 0)

        items: list[Item] = []
        y = 8.0
        heading = (self.cfg.get("title") or "").strip()
        if heading:
            h = fm["heading"].height() + 8
            items.append(Item("title", QRectF(0, y, w, h)))
            s = fm["heading"].height()
            items.append(Item("eye", QRectF(w - PAD - s - 2, y + 4, s + 2, s)))
            y += h + 2

        box = d + 2 * HOVER_GROW
        self.pie_rect = QRectF((w - d) / 2, y + HOVER_GROW, d, d)
        items.append(Item("pie", QRectF((w - box) / 2, y, box, box)))
        y += box + 6

        if legend:
            rh = fm["row"].height() + 7
            assets = [a for a in self.store.accounts if a["amount"] >= 0]
            debts = [a for a in self.store.accounts if a["amount"] < 0]
            for a in assets + debts:
                items.append(Item("row", QRectF(0, y, w, rh), a))
                y += rh
        elif not self.store.accounts:
            pass
        self.items = items
        self._compute_spans()
        self.setFixedSize(int(w), int(y + 8))
        self.update()
        self.place()

    def _compute_spans(self):
        spans, start = [], 0.0
        for a, frac in model.slices(self.store.accounts):
            spans.append((a, start, frac * 360.0))
            start += frac * 360.0
        self._spans = spans

    def _item_at(self, pos) -> Item | None:
        p = QPointF(pos)
        hit = None
        for it in self.items:
            if it.rect.contains(p):
                hit = it
        return hit

    def _pie_hit(self, pos) -> tuple[str, dict | None]:
        """('hole' | 'slice' | 'ring' | '', account) for a point."""
        c = self.pie_rect.center()
        dx, dy = pos.x() - c.x(), pos.y() - c.y()
        r = math.hypot(dx, dy)
        r_out = self.pie_rect.width() / 2
        r_in = r_out * HOLE
        if r <= r_in:
            return "hole", None
        if r > r_out + HOVER_GROW:
            return "", None
        cw = (90 - math.degrees(math.atan2(-dy, dx))) % 360
        for a, start, span in self._spans:
            if start <= cw < start + span:
                return "slice", a
        return "ring", None

    # ------------------------------------------------------------------ #
    # painting
    # ------------------------------------------------------------------ #
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 10, 10)
        p.fillPath(path, BG)
        p.setPen(QPen(QColor(255, 255, 255, 26), 1.0))
        p.drawPath(path)

        fonts = self._fonts()
        for it in self.items:
            getattr(self, f"_paint_{it.kind}")(p, it, fonts)

    def _paint_title(self, p, it, fonts):
        p.setFont(fonts["heading"])
        p.setPen(TEXT)
        p.drawText(it.rect.adjusted(PAD + 2, 0, -PAD - 30, 0),
                   Qt.AlignLeft | Qt.AlignVCenter, (self.cfg.get("title") or "").strip())

    def _paint_eye(self, p, it, _fonts):
        hover = self._hover_kind == "eye"
        c = QColor(TEXT if hover else MUTED)
        r = it.rect.adjusted(1, it.rect.height() * 0.2, -1, -it.rect.height() * 0.2)
        eye = QPainterPath()
        eye.moveTo(r.left(), r.center().y())
        eye.quadTo(r.center().x(), r.top() - r.height() * 0.55, r.right(), r.center().y())
        eye.quadTo(r.center().x(), r.bottom() + r.height() * 0.55, r.left(), r.center().y())
        p.setPen(QPen(c, 1.4))
        p.setBrush(Qt.NoBrush)
        p.drawPath(eye)
        p.setBrush(c)
        rad = r.height() * 0.28
        p.drawEllipse(r.center(), rad, rad)
        if self.cfg.get("private"):
            p.setPen(QPen(c, 1.6, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(r.left() + 1, r.bottom() + 2), QPointF(r.right() - 1, r.top() - 2))

    def _ring_slice(self, start: float, span: float, grow: float = 0.0) -> QPainterPath:
        c = self.pie_rect.center()
        r_out = self.pie_rect.width() / 2 + grow
        r_in = self.pie_rect.width() / 2 * HOLE
        outer = QRectF(c.x() - r_out, c.y() - r_out, 2 * r_out, 2 * r_out)
        inner = QRectF(c.x() - r_in, c.y() - r_in, 2 * r_in, 2 * r_in)
        qs = 90 - start
        path = QPainterPath()
        if span >= 359.99:
            path.addEllipse(outer)
            path.addEllipse(inner)        # odd-even fill leaves the hole
            return path
        path.arcMoveTo(outer, qs)
        path.arcTo(outer, qs, -span)
        path.arcTo(inner, qs - span, span)
        path.closeSubpath()
        return path

    def _paint_pie(self, p, _it, fonts):
        p.setPen(Qt.NoPen)
        if not self._spans:
            p.setBrush(TRACK)
            p.drawPath(self._ring_slice(0, 360))
        gap = 1.4 if len(self._spans) > 1 else 0.0
        for a, start, span in self._spans:
            g = min(gap, span * 0.3)
            hover = a["id"] == self._hover_id
            col = QColor(a["color"])
            if self._hover_id and not hover:
                col.setAlpha(150)
            p.setBrush(col)
            p.drawPath(self._ring_slice(start + g / 2, max(0.2, span - g),
                                        HOVER_GROW if hover else 0.0))
        self._paint_center(p, fonts)

    def _fit_font(self, text: str, base: QFont, px: int, max_w: float) -> QFont:
        f = QFont(base)
        f.setBold(True)
        while px > 8:
            f.setPixelSize(px)
            if QFontMetrics(f).horizontalAdvance(text) <= max_w:
                break
            px -= 1
        return f

    def _paint_center(self, p, fonts):
        c = self.pie_rect.center()
        hole = self.pie_rect.width() * HOLE
        max_w = hole * 0.84
        acc = self.store.get(self._hover_id) if self._hover_id else None
        net, assets, debts = model.totals(self.store.accounts)

        lines: list[tuple[QFont, QColor, str]] = []
        if acc is not None:
            share = acc["amount"] / assets if assets > 0 and acc["amount"] > 0 else None
            lines.append((fonts["label"], QColor(acc["color"]).lighter(115), acc["name"].upper()))
            amount = self.money(acc["amount"])
            lines.append((self._fit_font(amount, self.font(), int(hole * 0.2), max_w),
                          TEXT, amount))
            if share is not None:
                lines.append((fonts["small"], MUTED, f"{model.fmt_pct(share)} of total"))
            else:
                lines.append((fonts["small"], DOWN, "debt"))
        elif not self.store.accounts:
            lines.append((fonts["small"], MUTED, "Right-click"))
            lines.append((fonts["small"], MUTED, "→ Add account"))
        else:
            lines.append((fonts["label"], MUTED, "NET" if debts else "TOTAL"))
            amount = self.money(net)
            lines.append((self._fit_font(amount, self.font(), int(hole * 0.2), max_w),
                          TEXT, amount))
            ch = self._change()
            if ch is not None:
                delta, _since, base = ch
                arrow = "▲" if delta > 0 else "▼" if delta < 0 else "●"
                col = UP if delta > 0 else DOWN if delta < 0 else MUTED
                if self.cfg.get("private"):
                    txt = (f"{arrow} {abs(delta) / abs(base) * 100:.1f}%" if base
                           else arrow)
                else:
                    txt = f"{arrow} {model.fmt_money(abs(delta), self.cfg)}"
                lines.append((fonts["small"], col, txt))

        heights = [QFontMetrics(f).height() for f, _, _ in lines]
        y = c.y() - sum(heights) / 2
        for (f, col, text), h in zip(lines, heights):
            fm = QFontMetrics(f)
            p.setFont(f)
            p.setPen(col)
            p.drawText(QRectF(c.x() - max_w / 2, y, max_w, h), Qt.AlignCenter,
                       fm.elidedText(text, Qt.ElideRight, int(max_w)))
            y += h

    def _change(self):
        if not self.cfg.get("show_change", True):
            return None
        net = model.totals(self.store.accounts)[0]
        ch = model.change(self.store.history, net, date.today(),
                          int(self.cfg.get("change_days", 30)))
        if ch is None:
            return None
        delta, since = ch
        return delta, since, net - delta

    def _paint_row(self, p, it, fonts):
        a = it.account
        r = it.rect.adjusted(6, 0, -6, 0)
        hover = a["id"] == self._hover_id
        if hover:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 16))
            p.drawRoundedRect(r, 5, 5)
        neg = a["amount"] < 0
        dot = 9
        p.setPen(Qt.NoPen)
        col = QColor(a["color"])
        if neg:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(col, 1.6))
        else:
            p.setBrush(col)
        p.drawEllipse(QRectF(PAD, r.center().y() - dot / 2, dot, dot))

        pct_fm = QFontMetrics(fonts["pct"])
        pct_w = pct_fm.horizontalAdvance("100%") + 4
        amount = self.money(a["amount"])
        amt_fm = QFontMetrics(fonts["row_b"])
        amt_w = amt_fm.horizontalAdvance(amount)
        x = PAD + dot + 8
        right = self.width() - PAD
        name_w = right - pct_w - 10 - amt_w - x - 8

        p.setFont(fonts["row"])
        p.setPen(TEXT)
        nfm = QFontMetrics(fonts["row"])
        p.drawText(QRectF(x, r.top(), name_w, r.height()), Qt.AlignLeft | Qt.AlignVCenter,
                   nfm.elidedText(a["name"] or "Untitled", Qt.ElideRight, int(max(10, name_w))))
        p.setFont(fonts["row_b"])
        p.setPen(DOWN.lighter(110) if neg else TEXT)
        p.drawText(QRectF(right - pct_w - 10 - amt_w, r.top(), amt_w, r.height()),
                   Qt.AlignRight | Qt.AlignVCenter, amount)
        _, assets, _ = model.totals(self.store.accounts)
        if not neg and assets > 0:
            p.setFont(fonts["pct"])
            p.setPen(MUTED)
            p.drawText(QRectF(right - pct_w, r.top(), pct_w, r.height()),
                       Qt.AlignRight | Qt.AlignVCenter,
                       model.fmt_pct(a["amount"] / assets))

    # ------------------------------------------------------------------ #
    # tooltips / mouse
    # ------------------------------------------------------------------ #
    def _target(self, pos) -> tuple[str, dict | None]:
        it = self._item_at(pos)
        if it is None:
            return "", None
        if it.kind == "pie":
            return self._pie_hit(QPointF(pos))
        if it.kind == "row":
            return "row", it.account
        return it.kind, None

    def _account_tip(self, a) -> str:
        _, assets, _ = model.totals(self.store.accounts)
        lines = [a["name"] or "Untitled",
                 self.money(a["amount"], decimals=2)]
        if a["amount"] > 0 and assets > 0:
            lines[-1] += f"  ·  {a['amount'] / assets * 100:.1f}% of assets"
        elif a["amount"] < 0:
            lines[-1] += "  ·  debt"
        if a.get("note"):
            lines.append(a["note"])
        lines.append(model.age_text(a.get("updated", "")))
        lines.append("\nDouble-click to set the amount")
        return "\n".join(lines)

    def _total_tip(self) -> str:
        net, assets, debts = model.totals(self.store.accounts)
        n = len(self.store.accounts)
        lines = [f"{'Net' if debts else 'Total'} of {n} account{'s' if n != 1 else ''}: "
                 f"{self.money(net, decimals=2)}"]
        if debts:
            lines.append(f"Assets {self.money(assets)}  ·  Debts {self.money(debts)}")
        ch = self._change()
        if ch is not None:
            delta, since, base = ch
            pct = f" ({delta / abs(base) * 100:+.1f}%)" if base else ""
            lines.append(f"Change since {since:%d %b %Y}: "
                         f"{self.money(delta, sign=True)}{pct}".replace(" 0", " "))
        lines.append("\nClick to " + ("show" if self.cfg.get("private") else "hide")
                     + " amounts")
        return "\n".join(lines)

    def event(self, e):
        if e.type() == QEvent.ToolTip:
            kind, acc = self._target(e.pos())
            tip = ""
            if acc is not None:
                tip = self._account_tip(acc)
            elif kind == "hole":
                tip = self._total_tip()
            elif kind == "eye":
                tip = "Show amounts" if self.cfg.get("private") else "Hide amounts"
            if tip:
                QToolTip.showText(e.globalPos(), tip, self)
            else:
                QToolTip.hideText()
            return True
        return super().event(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press_pos = e.globalPosition().toPoint()
            self._press_kind = self._target(e.position().toPoint())[0]
            self._drag_offset = self._press_pos - self.frameGeometry().topLeft()
            self._dragged = False
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag_offset is not None and e.buttons() & Qt.LeftButton:
            gp = e.globalPosition().toPoint()
            if not self._dragged and (gp - self._press_pos).manhattanLength() < 5:
                return
            self._dragged = True
            self.move(gp - self._drag_offset)
            return
        kind, acc = self._target(e.position().toPoint())
        hid = acc["id"] if acc else None
        if hid != self._hover_id or kind != self._hover_kind:
            self._hover_id, self._hover_kind = hid, kind
            self.setCursor(Qt.PointingHandCursor if kind in ("hole", "eye") else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, _):
        self._hover_id = self._hover_kind = None
        self.update()

    def mouseReleaseEvent(self, e):
        if self._drag_offset is None:
            return
        self._drag_offset = None
        if not self._dragged:
            if self._press_kind in ("hole", "eye"):
                self.toggle_private()
            return
        scr = QGuiApplication.screenAt(self.frameGeometry().center()) or self._screen()
        g = scr.availableGeometry()
        fg = self.frameGeometry()
        if abs(fg.right() - g.right()) <= SNAP_PX:
            self.cfg["dock"] = "right"
        elif abs(fg.left() - g.left()) <= SNAP_PX:
            self.cfg["dock"] = "left"
        else:
            self.cfg["dock"] = "none"
        self._save_position()
        self.place()
        self._save_position()

    def mouseDoubleClickEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        kind, acc = self._target(e.position().toPoint())
        if acc is not None:
            self.set_amount(acc["id"])
        elif kind in ("", "title", "ring"):
            self.edit_accounts()

    # ------------------------------------------------------------------ #
    # placement
    # ------------------------------------------------------------------ #
    def _screen(self):
        pos = self.cfg.get("position") or {}
        if pos.get("x") is not None and pos.get("y") is not None:
            s = QGuiApplication.screenAt(QPoint(int(pos["x"]) + 5, int(pos["y"]) + 5))
            if s is not None:
                return s
        return QGuiApplication.primaryScreen()

    def place(self) -> None:
        if self._dragged and self._drag_offset is not None:
            return
        g = self._screen().availableGeometry()
        margin = int(self.cfg.get("margin", 12))
        pos = self.cfg.get("position") or {}
        dock = self.cfg.get("dock", "left")
        w, h = self.width(), self.height()
        if pos.get("y") is not None:
            y = int(pos["y"])
        else:
            va = self.cfg.get("valign", "top")
            y = (g.top() + margin if va == "top" else
                 g.bottom() - h - margin + 1 if va == "bottom" else
                 g.top() + (g.height() - h) // 2)
        if dock == "right":
            x = g.right() - w - margin + 1
        elif dock == "left":
            x = g.left() + margin
        elif pos.get("x") is not None:
            x = int(pos["x"])
        else:
            x = g.left() + margin
        x = max(g.left(), min(x, g.right() - w + 1))
        y = max(g.top(), min(y, g.bottom() - h + 1))
        self.move(x, y)

    def _save_position(self) -> None:
        self.cfg["position"] = {"x": self.x(), "y": self.y()}
        cfg_mod.save(self.cfg)

    # ------------------------------------------------------------------ #
    # actions
    # ------------------------------------------------------------------ #
    def _check_disk(self):
        if self.store.changed_on_disk() and self.store.load():
            self.relayout()

    def _commit(self):
        self.store.save()
        self.relayout()

    def toggle_private(self):
        self.cfg["private"] = not self.cfg.get("private", False)
        cfg_mod.save(self.cfg)
        self._priv_action.setChecked(bool(self.cfg["private"]))
        self.update()

    def set_amount(self, aid: str):
        a = self.store.get(aid)
        if not a:
            return
        v, ok = QInputDialog.getDouble(self, a["name"] or "Amount",
                                       f"Amount in {a['name'] or 'this account'}:",
                                       float(a["amount"]), -1e13, 1e13, 2)
        if ok:
            model.set_amount(a, v)
            self._commit()

    def add_to(self, aid: str):
        a = self.store.get(aid)
        if not a:
            return
        v, ok = QInputDialog.getDouble(self, a["name"] or "Amount",
                                       "Amount to add (negative to subtract):",
                                       0.0, -1e13, 1e13, 2)
        if ok and v:
            model.set_amount(a, a["amount"] + v)
            self._commit()

    def delete_account(self, aid: str):
        a = self.store.get(aid)
        if not a:
            return
        if QMessageBox.question(self, "Delete account",
                                f"Delete “{a['name'] or 'Untitled'}”?",
                                QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) == QMessageBox.Yes:
            self.store.remove(aid)
            self._commit()

    def edit_accounts(self, select: str | None = None, new: bool = False):
        from .editor import EditorDialog
        if self._editor is not None:
            self._editor.raise_()
            self._editor.activateWindow()
            return
        dlg = EditorDialog(self.store.accounts, self.cfg, select=select, new=new)
        self._editor = dlg
        self.watch.stop()
        try:
            if dlg.exec():
                self.store.replace_all(dlg.edited())
                self.store.save()
        finally:
            self._editor = None
            self.watch.start()
            self.relayout()

    # ------------------------------------------------------------------ #
    # menu
    # ------------------------------------------------------------------ #
    def contextMenuEvent(self, e):
        m = QMenu(self)
        _kind, acc = self._target(e.pos())
        if acc is not None:
            m.addSection(acc["name"] or "Untitled")
            m.addAction("Set amount…", lambda i=acc["id"]: self.set_amount(i))
            m.addAction("Add / subtract…", lambda i=acc["id"]: self.add_to(i))
            m.addAction("Edit…", lambda i=acc["id"]: self.edit_accounts(select=i))
            m.addAction("Delete…", lambda i=acc["id"]: self.delete_account(i))
            m.addSeparator()
        m.addAction("Add account…", lambda: self.edit_accounts(new=True))
        m.addAction("Edit accounts…", self.edit_accounts)
        m.addSeparator()

        a = m.addAction("Hide amounts")
        a.setCheckable(True)
        a.setChecked(bool(self.cfg.get("private")))
        a.triggered.connect(self.toggle_private)
        a = m.addAction("Show list under the pie")
        a.setCheckable(True)
        a.setChecked(bool(self.cfg.get("legend", True)))
        a.toggled.connect(lambda on: self._set("legend", bool(on)))
        a = m.addAction("Show change under the total")
        a.setCheckable(True)
        a.setChecked(bool(self.cfg.get("show_change", True)))
        a.toggled.connect(lambda on: self._set("show_change", bool(on)))
        self._choices(m.addMenu("Change compared with"), "change_days",
                      ((7, "A week ago"), (30, "A month ago"), (90, "3 months ago"),
                       (365, "A year ago")))
        cm = m.addMenu("Currency format")
        grp = QActionGroup(cm)
        for label, sym, after, sep, mark in CURRENCIES:
            act = QAction(label, cm)
            act.setCheckable(True)
            act.setChecked(self.cfg.get("currency_symbol") == sym
                           and bool(self.cfg.get("symbol_after")) == after
                           and self.cfg.get("thousands_separator") == sep
                           and self.cfg.get("decimal_mark") == mark)
            act.triggered.connect(lambda _c, v=(sym, after, sep, mark): self._set_currency(*v))
            grp.addAction(act)
            cm.addAction(act)
        self._choices(m.addMenu("Decimals"), "decimals",
                      ((0, "Whole amounts"), (2, "Cents")))
        self._choices(m.addMenu("Pie size"), "pie_size",
                      ((150, "Small"), (190, "Normal"), (240, "Large"), (300, "Extra large")))
        m.addAction("Rename heading…", self._rename)
        m.addSeparator()
        self._choices(m.addMenu("Dock"), "dock",
                      (("left", "Left edge"), ("right", "Right edge"),
                       ("none", "Floating (drag anywhere)")), self._set_dock)
        self._choices(m.addMenu("Vertical position"), "valign",
                      (("top", "Top"), ("center", "Middle"), ("bottom", "Bottom")),
                      self._set_valign,
                      checked=(self.cfg.get("position") or {}).get("y") is None)
        self._choices(m.addMenu("Window layer"), "window_layer",
                      (("top", "Always on top"), ("normal", "Normal (can be covered)"),
                       ("bottom", "Always at the bottom")), self._set_layer)
        a = m.addAction("Click-through")
        a.setCheckable(True)
        a.setChecked(bool(self.cfg.get("click_through", False)))
        a.toggled.connect(self._set_click_through)
        self._choices(m.addMenu("Opacity"), "opacity",
                      tuple((v, f"{int(v * 100)}%") for v in (1.0, 0.93, 0.8, 0.65, 0.5)),
                      self._set_opacity)
        self._choices(m.addMenu("Text size"), "font_size",
                      tuple((v, f"{v} pt") for v in (8, 9, 10, 11, 12, 14)))
        m.addSeparator()
        m.addAction("Open data file", self._open_data)
        m.addAction("Reset position", self._reset_position)
        m.addAction("Quit", self.quit)
        m.exec(e.globalPos())

    def _choices(self, menu, key, options, handler=None, checked=True):
        handler = handler or (lambda k, v: self._set(k, v))
        grp = QActionGroup(menu)
        cur = self.cfg.get(key)
        for value, text in options:
            act = QAction(text, menu)
            act.setCheckable(True)
            same = abs(float(cur) - value) < 0.01 if isinstance(value, float) else cur == value
            act.setChecked(checked and same)
            act.triggered.connect(lambda _c, v=value, k=key: handler(k, v))
            grp.addAction(act)
            menu.addAction(act)

    # ------------------------------------------------------------------ #
    # settings
    # ------------------------------------------------------------------ #
    def _set(self, key, value):
        self.cfg[key] = value
        cfg_mod.save(self.cfg)
        self.relayout()

    def _set_currency(self, sym, after, sep, mark):
        self.cfg.update({"currency_symbol": sym, "symbol_after": after,
                         "thousands_separator": sep, "decimal_mark": mark})
        cfg_mod.save(self.cfg)
        self.relayout()

    def _rename(self):
        text, ok = QInputDialog.getText(self, "Heading", "Heading (blank hides it):",
                                        text=self.cfg.get("title", ""))
        if ok:
            self._set("title", text.strip())

    def _set_dock(self, _key, value):
        self.cfg["dock"] = value
        if value == "none":
            self.cfg["position"] = {"x": self.x(), "y": self.y()}
        cfg_mod.save(self.cfg)
        self.place()
        self._save_position()

    def _set_valign(self, _key, value):
        self.cfg["valign"] = value
        self.cfg["position"] = {"x": self.x(), "y": None}
        if self.cfg.get("dock") == "none":
            self.cfg["dock"] = "left"
        cfg_mod.save(self.cfg)
        self.place()

    def _apply_flags(self):
        layer = self.cfg.get("window_layer", "normal")
        self.setWindowFlag(Qt.WindowStaysOnTopHint, layer == "top")
        self.setWindowFlag(Qt.WindowStaysOnBottomHint, layer == "bottom")
        self.setWindowFlag(Qt.WindowTransparentForInput,
                           bool(self.cfg.get("click_through", False)))

    def _set_layer(self, _key, value):
        self.cfg["window_layer"] = value
        cfg_mod.save(self.cfg)
        self._apply_flags()
        self.show()

    def _set_click_through(self, on):
        self.cfg["click_through"] = bool(on)
        cfg_mod.save(self.cfg)
        self._apply_flags()
        self.show()
        if self._ct_action.isChecked() != bool(on):
            self._ct_action.setChecked(bool(on))

    def _set_opacity(self, _key, v):
        self.cfg["opacity"] = v
        self.setWindowOpacity(v)
        cfg_mod.save(self.cfg)

    def _reset_position(self):
        self.cfg["dock"] = "left"
        self.cfg["valign"] = "top"
        self.cfg["position"] = {"x": None, "y": None}
        cfg_mod.save(self.cfg)
        self.place()
        self.show()

    def _open_data(self):
        path = str(self.store.path)
        try:
            if sys.platform.startswith("win"):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(path)])
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # tray
    # ------------------------------------------------------------------ #
    def _tray(self):
        icon = QIcon(_tray_icon())
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("Budget Pie")
        menu = QMenu()
        menu.addAction("Edit accounts…", self.edit_accounts)
        self._priv_action = menu.addAction("Hide amounts")
        self._priv_action.setCheckable(True)
        self._priv_action.setChecked(bool(self.cfg.get("private")))
        self._priv_action.triggered.connect(lambda _c: self.toggle_private())
        menu.addAction("Bring to front", self._bring_to_front)
        menu.addAction("Show / hide", lambda: self.setVisible(not self.isVisible()))
        self._ct_action = menu.addAction("Click-through")
        self._ct_action.setCheckable(True)
        self._ct_action.setChecked(bool(self.cfg.get("click_through", False)))
        self._ct_action.toggled.connect(
            lambda on: on != bool(self.cfg.get("click_through")) and self._set_click_through(on))
        menu.addAction("Reset position", self._reset_position)
        menu.addSeparator()
        menu.addAction("Quit", self.quit)
        self._tray_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda r: self.setVisible(not self.isVisible())
            if r == QSystemTrayIcon.Trigger else None)
        self.tray.show()
        self.setWindowIcon(icon)

    def _bring_to_front(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def quit(self):
        self.tray.hide()
        QApplication.quit()

    def closeEvent(self, e):
        self.quit()
        e.accept()


def _tray_icon() -> QPixmap:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    rect = QRectF(4, 4, 56, 56)
    start = 90
    for frac, col in ((0.45, "#7C6CE8"), (0.3, "#56D07A"), (0.15, "#3FC1E0"),
                      (0.10, "#E0A03F")):
        span = frac * 360
        path = QPainterPath()
        path.arcMoveTo(rect, start)
        path.arcTo(rect, start, -span)
        inner = rect.adjusted(17, 17, -17, -17)
        path.arcTo(inner, start - span, span)
        path.closeSubpath()
        p.fillPath(path, QColor(col))
        start -= span
    p.end()
    return pm
