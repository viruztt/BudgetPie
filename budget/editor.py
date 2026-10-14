"""Add / edit / reorder accounts, with a running total."""
from __future__ import annotations

import copy

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QColorDialog, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from . import model

SURFACE = "#14151a"
PANEL = "#1a1c24"
INK = "#eef0f5"
INK_MUTED = "#7c8394"

STYLE = f"""
QDialog {{ background: {SURFACE}; }}
QLabel {{ color: {INK}; }}
QLabel#hint {{ color: {INK_MUTED}; }}
QLabel#total {{ color: {INK}; font-weight: bold; }}
QListWidget {{
    background: {PANEL}; color: {INK}; border: 1px solid rgba(255,255,255,0.10);
    border-radius: 6px; padding: 4px; outline: none;
}}
QListWidget::item {{ padding: 5px 4px; border-radius: 4px; }}
QListWidget::item:selected {{ background: rgba(63,193,224,0.22); color: {INK}; }}
QLineEdit, QDoubleSpinBox, QPushButton {{
    background: {PANEL}; color: {INK};
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 5px; padding: 4px 8px; min-height: 18px;
}}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 0px; border: none; }}
QPushButton:hover {{ border-color: rgba(255,255,255,0.3); }}
QPushButton#swatch {{ border-radius: 11px; padding: 0; min-height: 0; }}
"""


def _dot(color: str, hollow: bool = False, size: int = 14) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    if hollow:
        p.setPen(QPen(QColor(color), 2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(2, 2, size - 4, size - 4)
    else:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(1, 1, size - 2, size - 2)
    p.end()
    return QIcon(pm)


class EditorDialog(QDialog):
    def __init__(self, accounts: list[dict], cfg: dict, select: str | None = None,
                 new: bool = False, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.items = copy.deepcopy(accounts)
        self._loading = False
        self.setWindowTitle("Budget Pie — accounts")
        self.setStyleSheet(STYLE)
        self.resize(720, 420)

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(14)

        # ---- left --------------------------------------------------------- #
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setIconSize(QSize(14, 14))
        self.list.currentRowChanged.connect(lambda _r: self._on_select())
        self.list.model().rowsMoved.connect(self._on_reorder)
        left.addWidget(self.list, 1)
        self.total = QLabel()
        self.total.setObjectName("total")
        self.total.setWordWrap(True)
        left.addWidget(self.total)
        hint = QLabel("Drag to reorder — the pie follows this order")
        hint.setObjectName("hint")
        left.addWidget(hint)
        row = QHBoxLayout()
        b = QPushButton("Add")
        b.clicked.connect(self._add)
        row.addWidget(b)
        self.dup_btn = QPushButton("Duplicate")
        self.dup_btn.clicked.connect(self._duplicate)
        row.addWidget(self.dup_btn)
        self.del_btn = QPushButton("Delete")
        self.del_btn.clicked.connect(self._delete)
        row.addWidget(self.del_btn)
        left.addLayout(row)
        lw = QWidget()
        lw.setLayout(left)
        lw.setFixedWidth(290)
        root.addWidget(lw)

        # ---- right -------------------------------------------------------- #
        right = QVBoxLayout()
        self.form_box = QWidget()
        f = QFormLayout(self.form_box)
        f.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        f.setHorizontalSpacing(12)
        f.setVerticalSpacing(9)

        self.name = QLineEdit()
        self.name.setPlaceholderText("Checking, Savings, Cash, Brokerage…")
        self.name.textChanged.connect(self._changed)
        f.addRow("Name", self.name)

        self.amount = QDoubleSpinBox()
        self.amount.setRange(-1e13, 1e13)
        self.amount.setDecimals(2)
        self.amount.setGroupSeparatorShown(True)
        self.amount.setKeyboardTracking(False)
        sym = cfg.get("currency_symbol", "€")
        if cfg.get("symbol_after"):
            self.amount.setSuffix(f" {sym}")
        else:
            self.amount.setPrefix(sym)
        self.amount.valueChanged.connect(self._changed)
        f.addRow("Amount", self.amount)

        sw = QHBoxLayout()
        sw.setSpacing(5)
        sw.setContentsMargins(0, 0, 0, 0)
        self.swatches = []
        for c in model.PALETTE:
            b = QPushButton()
            b.setObjectName("swatch")
            b.setFixedSize(22, 22)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, col=c: self._set_color(col))
            self.swatches.append((c, b))
            sw.addWidget(b)
        more = QPushButton("…")
        more.setFixedWidth(32)
        more.setToolTip("Custom colour")
        more.clicked.connect(self._pick_color)
        sw.addWidget(more)
        sw.addStretch(1)
        swr = QWidget()
        swr.setLayout(sw)
        f.addRow("Colour", swr)

        self.note = QLineEdit()
        self.note.setPlaceholderText("Optional — bank, account number hint…")
        self.note.textChanged.connect(self._changed)
        f.addRow("Note", self.note)

        self.updated = QLabel()
        self.updated.setObjectName("hint")
        f.addRow("", self.updated)
        right.addWidget(self.form_box)

        h = QLabel("A negative amount (credit card, loan) is left out of the pie, "
                   "listed in red and subtracted from the total.")
        h.setObjectName("hint")
        h.setWordWrap(True)
        right.addWidget(h)
        right.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Save")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        right.addWidget(buttons)
        root.addLayout(right, 1)

        self._fill()
        if new:
            self._add()
        else:
            idx = next((i for i, a in enumerate(self.items) if a["id"] == select), 0)
            self.list.setCurrentRow(idx if self.items else -1)
        self._sync()

    # ------------------------------------------------------------------ #
    def _money(self, v, decimals=2):
        return model.fmt_money(v, self.cfg, decimals)

    def _label(self, a) -> str:
        return f"{a['name'] or 'Untitled'}  —  {self._money(a['amount'], None)}"

    def _item(self, a) -> QListWidgetItem:
        it = QListWidgetItem(_dot(a["color"], a["amount"] < 0), self._label(a))
        it.setData(Qt.UserRole, a["id"])
        return it

    def _fill(self):
        self.list.blockSignals(True)
        self.list.clear()
        for a in self.items:
            self.list.addItem(self._item(a))
        self.list.blockSignals(False)

    def _current(self) -> dict | None:
        it = self.list.currentItem()
        if it is None:
            return None
        aid = it.data(Qt.UserRole)
        return next((a for a in self.items if a["id"] == aid), None)

    def _on_reorder(self, *_):
        order = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]
        by_id = {a["id"]: a for a in self.items}
        self.items = [by_id[i] for i in order if i in by_id]

    def _sync(self):
        has = self._current() is not None
        for w in (self.form_box, self.dup_btn, self.del_btn):
            w.setEnabled(has)
        net, assets, debts = model.totals(self.items)
        text = f"{'Net' if debts else 'Total'}: {self._money(net)}"
        if debts:
            text += (f"<br><span style='color:{INK_MUTED}; font-weight:normal'>"
                     f"assets {self._money(assets, None)} \u00b7 "
                     f"debts {self._money(debts, None)}</span>")
        self.total.setText(text)

    def _add(self):
        a = model.new_account("", 0, model.next_color(self.items))
        self.items.append(a)
        self.list.addItem(self._item(a))
        self.list.setCurrentRow(self.list.count() - 1)
        self.name.setFocus()
        self._sync()

    def _duplicate(self):
        a = self._current()
        if not a:
            return
        c = copy.deepcopy(a)
        c["id"] = model.new_id()
        c["name"] = (a["name"] or "Untitled") + " (copy)"
        c["color"] = model.next_color(self.items)
        row = self.list.currentRow() + 1
        self.items.insert(row, c)
        self.list.insertItem(row, self._item(c))
        self.list.setCurrentRow(row)
        self._sync()

    def _delete(self):
        a = self._current()
        if not a:
            return
        if QMessageBox.question(self, "Delete account",
                                f"Delete “{a['name'] or 'Untitled'}”?",
                                QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) != QMessageBox.Yes:
            return
        row = self.list.currentRow()
        self.items = [x for x in self.items if x["id"] != a["id"]]
        self.list.takeItem(row)
        self._sync()

    def _on_select(self):
        a = self._current()
        self._sync()
        if not a:
            return
        self._loading = True
        try:
            self.name.setText(a["name"])
            self.amount.setValue(float(a["amount"]))
            self.note.setText(a.get("note", ""))
            self._mark_swatch(a["color"])
            self.updated.setText(model.age_text(a.get("updated", "")).capitalize())
        finally:
            self._loading = False

    def _changed(self, *_):
        if self._loading:
            return
        a = self._current()
        if not a:
            return
        a["name"] = self.name.text().strip()
        a["note"] = self.note.text().strip()
        v = round(self.amount.value(), 2)
        if v != a["amount"]:
            model.set_amount(a, v)
            self.updated.setText(model.age_text(a["updated"]).capitalize())
        it = self.list.currentItem()
        if it:
            it.setText(self._label(a))
            it.setIcon(_dot(a["color"], a["amount"] < 0))
        self._sync()

    def _mark_swatch(self, color: str):
        for c, b in self.swatches:
            ring = "#ffffff" if c.upper() == color.upper() else "rgba(0,0,0,0)"
            b.setStyleSheet(f"background: {c}; border: 2px solid {ring};")

    def _set_color(self, color: str):
        a = self._current()
        if not a:
            return
        a["color"] = color
        self._mark_swatch(color)
        it = self.list.currentItem()
        if it:
            it.setIcon(_dot(color, a["amount"] < 0))

    def _pick_color(self):
        a = self._current()
        if not a:
            return
        c = QColorDialog.getColor(QColor(a["color"]), self, "Slice colour")
        if c.isValid():
            self._set_color(c.name().upper())

    def edited(self) -> list[dict]:
        self._on_reorder()
        for a in self.items:
            if not a["name"]:
                a["name"] = "Untitled"
        return self.items
