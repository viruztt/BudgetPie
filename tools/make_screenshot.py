"""Render the widget with example accounts to docs/screenshot.png.

Uses made-up amounts, never your own data/accounts.json.
    python tools/make_screenshot.py [output.png]
Set QT_SCALE_FACTOR=2 for a high-DPI image.
"""
import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

app_dir = Path(__file__).resolve().parent.parent
out = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else app_dir / "docs" / "screenshot.png"
out.parent.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(app_dir))

from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter
from PySide6.QtWidgets import QApplication

from budget import config as cfg_mod
from budget import model, theme
from budget.store import Store
from budget.widget import BudgetWindow

P = model.PALETTE
accounts = [
    model.new_account("Checking account", 2400, P[0]),
    model.new_account("Savings", 8000, P[1]),
    model.new_account("Investments", 12500, P[3]),
    model.new_account("Cash", 350, P[2]),
]
month_ago = (date.today() - timedelta(days=31)).isoformat()
path = Path(tempfile.mkdtemp()) / "accounts.json"
path.write_text(json.dumps({"version": 1, "accounts": accounts,
                            "history": [[month_ago, 21000]]}), encoding="utf-8")

cfg = json.loads(json.dumps(cfg_mod.DEFAULTS))
cfg.update({"data_path": str(path), "dock": "none", "opacity": 1.0,
            "private": False, "position": {"x": 40, "y": 40}})

app = QApplication([])
theme.apply(app)
win = BudgetWindow(cfg, Store(path, seed=False))
win.show()
app.processEvents()

img = win.grab().toImage()
dpr = img.devicePixelRatio()
img.setDevicePixelRatio(1)
pad = int(28 * dpr)
bg = QImage(img.width() + 2 * pad, img.height() + 2 * pad, QImage.Format_ARGB32)
p = QPainter(bg)
g = QLinearGradient(0, 0, bg.width(), bg.height())
g.setColorAt(0, QColor("#1f3b57"))
g.setColorAt(1, QColor("#3a2448"))
p.fillRect(bg.rect(), g)
p.drawImage(QPoint(pad, pad), img)
p.end()
bg.save(str(out))
print(out, bg.width(), bg.height())
