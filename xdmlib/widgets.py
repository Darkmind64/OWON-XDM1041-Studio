# -*- coding: utf-8 -*-
"""Icône, thèmes et afficheur flottant."""
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt
import pyqtgraph as pg

THEMES = {
    "dark": dict(win="#1e1f22", text="#e0e0e0", base="#141517", alt="#25262a", btn="#2b2d31", hi="#2f7d4a", plot="#0d0e10", fg="#c8c8c8", curve="#4cff7a"),
    "light": dict(win="#f2f3f5", text="#1b1c1e", base="#ffffff", alt="#eceef1", btn="#e4e6ea", hi="#2f9e58", plot="#ffffff", fg="#333333", curve="#0a8f3c"),
}


def draw_icon(size=256):
    img = QtGui.QImage(size, size, QtGui.QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    s = size / 256
    p.setBrush(QtGui.QColor("#23262b"))
    p.setPen(QtGui.QPen(QtGui.QColor("#4cff7a"), 6 * s))
    p.drawRoundedRect(QtCore.QRectF(14 * s, 14 * s, 228 * s, 228 * s), 36 * s, 36 * s)
    p.setPen(Qt.NoPen)
    p.setBrush(QtGui.QColor("#05080a"))
    p.drawRoundedRect(QtCore.QRectF(36 * s, 40 * s, 184 * s, 96 * s), 14 * s, 14 * s)
    pen = QtGui.QPen(QtGui.QColor("#4cff7a"), 9 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    path = QtGui.QPainterPath(QtCore.QPointF(48 * s, 100 * s))
    for x, y in ((80, 100), (96, 60), (118, 120), (140, 70), (160, 100), (208, 100)):
        path.lineTo(x * s, y * s)
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    for cx, col in ((80, "#ff5050"), (128, "#e0e0e0"), (176, "#4cff7a")):
        p.setPen(Qt.NoPen)
        p.setBrush(QtGui.QColor(col))
        p.drawEllipse(QtCore.QPointF(cx * s, 190 * s), 17 * s, 17 * s)
    p.end()
    return img


def app_icon():
    ic = QtGui.QIcon()
    for sz in (16, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(QtGui.QPixmap.fromImage(draw_icon(sz)))
    return ic


def apply_theme(app, name):
    c = THEMES[name]
    app.setStyle("Fusion")
    p = QtGui.QPalette()
    R = QtGui.QPalette.ColorRole
    for role, col in ((R.Window, c["win"]), (R.WindowText, c["text"]), (R.Base, c["base"]), (R.AlternateBase, c["alt"]),
                      (R.Text, c["text"]), (R.Button, c["btn"]), (R.ButtonText, c["text"]), (R.Highlight, c["hi"]),
                      (R.HighlightedText, "#ffffff"), (R.ToolTipBase, c["btn"]), (R.ToolTipText, c["text"]),
                      (R.PlaceholderText, "#888888")):
        p.setColor(role, QtGui.QColor(col))
    app.setPalette(p)
    app.setStyleSheet(f"QPushButton:checked{{background:{c['hi']};color:white;}} QGroupBox{{font-weight:bold;}}")
    pg.setConfigOptions(antialias=True, background=c["plot"], foreground=c["fg"])
    return c


class FloatingDisplay(QtWidgets.QWidget):
    closed = QtCore.Signal()

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("XDM1041 — afficheur")
        self.frame = QtWidgets.QFrame(self)
        self.frame.setStyleSheet("QFrame{background:rgba(5,8,10,215);border:1px solid #2f7d4a;border-radius:10px;}"
                                 "QLabel{border:none;background:transparent;}")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.frame)
        g = QtWidgets.QGridLayout(self.frame)
        g.setContentsMargins(12, 6, 12, 8)
        self.lb_mode = QtWidgets.QLabel("")
        self.lb_mode.setStyleSheet("color:#aaa;font:10pt 'Segoe UI';")
        self.lb_pf = QtWidgets.QLabel("")
        self.lb_val = QtWidgets.QLabel("-----", alignment=Qt.AlignRight | Qt.AlignVCenter)
        self.lb_val.setStyleSheet("color:#4cff7a;font:bold 30pt Consolas;")
        self.lb_sec = QtWidgets.QLabel("", alignment=Qt.AlignRight)
        self.lb_sec.setStyleSheet("color:#7ad7ff;font:11pt Consolas;")
        g.addWidget(self.lb_mode, 0, 0)
        g.addWidget(self.lb_pf, 0, 1, alignment=Qt.AlignRight)
        g.addWidget(self.lb_val, 1, 0, 1, 2)
        g.addWidget(self.lb_sec, 2, 0, 1, 2)
        self._drag = None
        self.setWindowOpacity(0.92)
        self.resize(330, 120)

    def update_view(self, val, mode, sec, pf, color="#4cff7a"):
        self.lb_val.setText(val)
        self.lb_val.setStyleSheet(f"color:{color};font:bold 30pt Consolas;")
        self.lb_mode.setText(mode)
        self.lb_sec.setText(sec or "")
        if pf:
            self.lb_pf.setText(pf)
            self.lb_pf.setStyleSheet(f"color:#05080a;background:{'#4cff7a' if pf == 'PASS' else '#ff5050'};"
                                     "border-radius:5px;padding:0 6px;font:bold 10pt 'Segoe UI';")
        else:
            self.lb_pf.setText("")
            self.lb_pf.setStyleSheet("")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def contextMenuEvent(self, e):
        m = QtWidgets.QMenu(self)
        for pct in (100, 85, 70, 50):
            m.addAction(f"Opacité {pct} %", lambda p=pct: self.setWindowOpacity(p / 100))
        m.addSeparator()
        m.addAction("Fermer", self.close)
        m.exec(e.globalPos())

    def closeEvent(self, e):
        self.closed.emit()
        super().closeEvent(e)
