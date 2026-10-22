# -*- coding: utf-8 -*-
"""Icône de l'application et afficheur flottant."""
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt
from . import theme as TH
from .theme import mix
from .ui import mono, ui_font


def draw_icon(size=256):
    img = QtGui.QImage(size, size, QtGui.QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    s = size / 256
    p.setBrush(QtGui.QColor("#16191e"))
    p.setPen(QtGui.QPen(QtGui.QColor("#3ddc84"), 10 * s))
    p.drawRoundedRect(QtCore.QRectF(14 * s, 14 * s, 228 * s, 228 * s), 52 * s, 52 * s)
    pen = QtGui.QPen(QtGui.QColor("#3ddc84"), 18 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    path = QtGui.QPainterPath(QtCore.QPointF(48 * s, 140 * s))
    for x, y in ((92, 140), (114, 76), (146, 184), (168, 112), (182, 140), (208, 140)):
        path.lineTo(x * s, y * s)
    p.drawPath(path)
    p.end()
    return img


def app_icon():
    ic = QtGui.QIcon()
    for sz in (16, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(QtGui.QPixmap.fromImage(draw_icon(sz)))
    return ic


class FloatingDisplay(QtWidgets.QWidget):
    closed = QtCore.Signal()

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("XDM1041 — afficheur")
        self.frame = QtWidgets.QFrame(self)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.frame)
        g = QtWidgets.QGridLayout(self.frame)
        g.setContentsMargins(14, 8, 14, 10)
        self.lb_mode = QtWidgets.QLabel("")
        self.lb_pf = QtWidgets.QLabel("")
        self.lb_val = QtWidgets.QLabel("-----", alignment=Qt.AlignRight | Qt.AlignVCenter)
        self.lb_sec = QtWidgets.QLabel("", alignment=Qt.AlignRight)
        g.addWidget(self.lb_mode, 0, 0)
        g.addWidget(self.lb_pf, 0, 1, alignment=Qt.AlignRight)
        g.addWidget(self.lb_val, 1, 0, 1, 2)
        g.addWidget(self.lb_sec, 2, 0, 1, 2)
        self._drag = None
        self._col = None
        self.setWindowOpacity(0.94)
        self.resize(340, 124)
        self.refresh_theme()

    def refresh_theme(self):
        t = TH.CURRENT
        c = QtGui.QColor(t["disp_bg"])
        self.frame.setStyleSheet(f"QFrame{{background: rgba({c.red()},{c.green()},{c.blue()},225); border: 1px solid {t['disp_border']}; border-radius: 12px;}}"
                                 "QLabel{border: none; background: transparent;}")
        self.lb_mode.setStyleSheet(f"color: {t['muted']};")
        self.lb_mode.setFont(ui_font(12))
        self.lb_val.setFont(mono(34, "Share Tech" not in t["digit_font"]))
        self.lb_sec.setFont(mono(14))
        self.lb_sec.setStyleSheet(f"color: {t['sec']};")
        self._col = None

    def update_view(self, val, mode, sec, pf, color=None):
        t = TH.CURRENT
        col = color or t["digit"]
        self.lb_val.setText(val)
        if col != self._col:
            self._col = col
            self.lb_val.setStyleSheet(f"color: {col};")
        self.lb_mode.setText(mode)
        self.lb_sec.setText(sec or "")
        if pf:
            bg = t["pass_"] if pf == "PASS" else t["fail"]
            self.lb_pf.setText(pf)
            self.lb_pf.setStyleSheet(f"color: {TH.on_color(bg)}; background: {bg}; border-radius: 5px; padding: 1px 8px; font-weight: 700;")
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
