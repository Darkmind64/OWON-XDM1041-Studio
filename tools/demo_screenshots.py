# -*- coding: utf-8 -*-
"""Génère docs/screenshot.png et docs/sequences.png avec des mesures simulées (sans multimètre)."""
import os, sys, math, time, random
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from PySide6 import QtWidgets, QtCore
import xdm1041_studio as S

app = QtWidgets.QApplication(sys.argv)
app.setOrganizationName("Davy")
app.setApplicationName("XDM1041StudioDemo")
w = S.Main()
w.resize(1500, 950)
w.show()
w.set_mode("VDC", send=False)
w.lb_idn.setText("OWON,XDM1041,2312345,V4.3.0,3")
w.ck_lim.setChecked(True)
w.ed_lo.setText("4.99")
w.ed_hi.setText("5.02")
w.update_limits()
t0 = time.time() - 60
for i in range(1200):
    t = t0 + i * 0.05
    w.on_reading(t, 5.004 + 0.008 * math.sin(i / 40) + random.gauss(0, 0.0008), None)
w.refresh_views()
w.autoscale()
os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)


def snap(name):
    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(400, loop.quit)
    loop.exec()
    w.grab().save(os.path.join(ROOT, "docs", name))


snap("screenshot.png")
w.tabs.setCurrentIndex(7)
w.seq_out.setPlainText(
    "▶ Démarrage de la séquence\n[3] PROMPT \"Pointes sur la sortie 5 V, puis OK\"\n[4] MODE VDC\n[5] RANGE AUTO\n"
    "[6] WAIT_STABLE 0.2 5 15\n    stable : 5.00412 V\n[7] MEASURE v5 10\n    v5 = 5.00398 V\n[8] CHECK v5 4.75 5.25\n"
    "    ✔ v5 = 5.00398 V  [4.75 … 5.25]\n[9] LOG \"Tension continue : {v5}\"\n    Tension continue : 5.00398 V\n"
    "[10] MODE VAC\n[12] MEASURE ondulation 10\n    ondulation = 21.3047 mV\n[13] CHECK ondulation 0 50m\n"
    "    ✔ ondulation = 21.3047 mV  [0 … 50m]\n=== Verdict : PASS ===")
w.lb_verdict.setText("PASS")
w.lb_verdict.setStyleSheet("color:#2fbf5f;")
snap("sequences.png")
QtCore.QSettings("Davy", "XDM1041StudioDemo").clear()
print("OK")
