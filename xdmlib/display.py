# -*- coding: utf-8 -*-
"""Afficheur principal : style Atelier (moderne) ou Instrument (LCD à voyants et barregraphe)."""
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt
from . import theme as TH
from .theme import mix
from .ui import BarGraph, mono, ui_font, T

class Chip(QtWidgets.QLabel):
    clicked = QtCore.Signal(str, QtCore.QPoint)

    def __init__(self):
        super().__init__(objectName="DispChip")
        self.key = ""
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.key, self.mapToGlobal(QtCore.QPoint(0, self.height() + 2)))


ANNUN = ["AUTO", "HOLD", "REL", "AVG", "MATH", "dB", "2ND", "REC", "RMT"]


class DisplayWidget(QtWidgets.QFrame):
    chip_clicked = QtCore.Signal(str, QtCore.QPoint)

    def __init__(self, parent=None, preview=False):
        super().__init__(parent)
        self.setObjectName("Display")
        self.preview = preview
        self.skin = "atelier"
        self.num, self.unit_txt, self.color = "-----", "V", None
        self.blink_on = True
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(24, 14, 26, 14)
        v.setSpacing(4)

        top = QtWidgets.QHBoxLayout()
        top.setSpacing(8)
        self.chip_box = QtWidgets.QHBoxLayout()
        self.chip_box.setSpacing(8)
        self.chips = []
        self.annun_box = QtWidgets.QHBoxLayout()
        self.annun_box.setSpacing(12)
        self.annun = {}
        for a in ANNUN:
            lab = QtWidgets.QLabel(a)
            self.annun[a] = lab
            self.annun_box.addWidget(lab)
        self.rate = QtWidgets.QLabel("")
        top.addLayout(self.chip_box)
        top.addLayout(self.annun_box)
        top.addStretch()
        top.addWidget(self.rate)
        v.addLayout(top)

        mid = QtWidgets.QHBoxLayout()
        mid.setSpacing(14)
        mid.addStretch()
        self.val = QtWidgets.QLabel("-----")
        self.val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.val.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        self.unit = QtWidgets.QLabel("V")
        self.unit.setAlignment(Qt.AlignLeft | Qt.AlignBottom)
        mid.addWidget(self.val, 1)
        mid.addWidget(self.unit, 0, Qt.AlignVCenter)
        v.addLayout(mid, 1)

        barrow = QtWidgets.QHBoxLayout()
        barrow.setSpacing(12)
        self.bar = BarGraph()
        self.scale = QtWidgets.QLabel("")
        barrow.addWidget(self.bar, 1)
        barrow.addWidget(self.scale)
        self.bar_w = QtWidgets.QWidget(objectName="Transparent")
        self.bar_w.setLayout(barrow)
        barrow.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self.bar_w)

        self.statsline = QtWidgets.QLabel("")
        v.addWidget(self.statsline)

        bot = QtWidgets.QHBoxLayout()
        bot.setSpacing(14)
        self.pf = QtWidgets.QLabel("", objectName="PF")
        self.extra = QtWidgets.QLabel("", objectName="Muted")
        self.sec = QtWidgets.QLabel("")
        bot.addWidget(self.pf)
        bot.addWidget(self.extra)
        bot.addStretch()
        bot.addWidget(self.sec)
        v.addLayout(bot)

        self.glow = QtWidgets.QGraphicsDropShadowEffect(self.val)
        self.glow.setOffset(0, 0)
        self.val.setGraphicsEffect(self.glow)
        self.blink = QtCore.QTimer(self, interval=450, timeout=self._blink)
        self.blink.start()
        self.apply_theme()

    # ---------------------------------------------------------------- thème
    def apply_theme(self, skin=None):
        if skin:
            self.skin = skin
        t = TH.CURRENT
        inst = self.skin == "instrument"
        self.setProperty("skin", self.skin)
        self.style().unpolish(self)
        self.style().polish(self)
        for c in self.chips:
            c.setVisible(not inst)
        for a, lab in self.annun.items():
            lab.setVisible(inst)
            lab.setFont(mono(14))
        self.rate.setFont(mono(12))
        self.rate.setStyleSheet(f"color: {t.get('muted')};")
        self._auto_hide()
        self.scale.setFont(mono(16))
        self.scale.setStyleSheet(f"color: {mix(t.get('digit', '#888'), t.get('disp_bg', '#000'), 0.45)};")
        self.statsline.setFont(mono(16))
        self.statsline.setStyleSheet(f"color: {mix(t.get('digit', '#888'), t.get('disp_bg', '#000'), 0.45)};")
        self.sec.setFont(mono(20))
        self.sec.setStyleSheet(f"color: {t.get('sec')};")
        self.extra.setFont(ui_font(13))
        g = int(t.get("glow", 40))
        self.glow.setEnabled(g > 0)
        c = QtGui.QColor(t.get("digit", "#3ddc84"))
        c.setAlpha(min(255, int(g * 2.4)))
        self.glow.setColor(c)
        self.glow.setBlurRadius(4 + g * 0.45)
        self._last = None
        self._paint_value()
        self._refresh_annun()

    def _refresh_annun(self):
        inst = self.skin == "instrument"
        narrow = self.width() < 640
        for a, lab in self.annun.items():
            on = lab.property("on")
            lab.setStyleSheet(f"color: {T('digit') if on else T('annun_off')};")
            lab.setVisible(inst and (bool(on) or not narrow))

    # ---------------------------------------------------------------- données
    def set_value(self, num, unit, color=None):
        self.num, self.unit_txt, self.color = num, unit, color
        self._paint_value()

    def _paint_value(self):
        t = TH.CURRENT
        col = self.color or t.get("digit", "#3ddc84")
        if self.num == "OL" and t.get("blink_ol") and not self.blink_on:
            col = mix(col, t.get("disp_bg", "#000"), 0.75)
        self.val.setText(self.num)
        self.unit.setText(self.unit_txt)
        size = int(self._fit_size())
        key = (size, col, t.get("digit_font"), self.skin)
        if key == getattr(self, "_last", None):
            return
        self._last = key
        f = mono(size, "Share Tech" not in t.get("digit_font", ""))
        self.val.setFont(f)
        self.unit.setFont(mono(max(14, size * 0.4)))
        self.val.setStyleSheet(f"color: {col};")
        self.unit.setStyleSheet(f"color: {col}; padding-top: {int(size * 0.32)}px;")

    def _fit_size(self):
        want = int(TH.CURRENT.get("digit_size", 128))
        if self.preview:
            want = int(want * 0.6)
        avail_w = max(60, self.width() - 60)
        fixed = 28 + 26 + (34 if (self.pf.isVisible() or self.sec.text() or self.extra.text()) else 4)
        if self.bar_w.isVisible():
            fixed += 30
        if self.statsline.isVisible():
            fixed += 28
        avail_h = max(24, self.height() - fixed)
        n = max(5, len(self.num))
        fm = QtGui.QFontMetricsF(mono(100, True))
        w100 = fm.horizontalAdvance("8" * n) + 0.4 * fm.horizontalAdvance("8" * max(2, len(self.unit_txt))) + 14
        fit_w = avail_w / w100 * 100
        fit_h = avail_h * 100 / (fm.height() * 0.92)
        return max(18, min(want, fit_w, fit_h))

    def _auto_hide(self):
        inst = self.skin == "instrument"
        h = self.height()
        bar = inst or bool(TH.CURRENT.get("bargraph"))
        self.statsline.setVisible(inst and h >= 200)
        self.bar_w.setVisible(bar and h >= (150 if inst else 170))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._auto_hide()
        self._paint_value()
        if (e.size().width() < 640) != (e.oldSize().width() < 640):
            self._refresh_annun()

    def minimumSizeHint(self):
        return QtCore.QSize(240, 110)

    def _blink(self):
        if self.num == "OL" and TH.CURRENT.get("blink_ol"):
            self.blink_on = not self.blink_on
            self._paint_value()
        elif not self.blink_on:
            self.blink_on = True
            self._paint_value()

    def set_chips(self, items, tips=None):
        """items : liste de textes ou de (clé, texte, info-bulle)."""
        while len(self.chips) < len(items):
            c = Chip()
            c.clicked.connect(self.chip_clicked.emit)
            self.chip_box.addWidget(c)
            self.chips.append(c)
        for i, c in enumerate(self.chips):
            if i < len(items):
                it = items[i] if isinstance(items[i], (tuple, list)) else ("", items[i], "")
                c.key = it[0]
                c.setText(it[1])
                c.setToolTip(it[2] if len(it) > 2 else "")
                c.setProperty("accent", i == 0)
                c.setProperty("warn", c.key == "capt")
                c.style().unpolish(c)
                c.style().polish(c)
                c.setVisible(self.skin != "instrument")
            else:
                c.setVisible(False)

    def set_annunciators(self, on):
        changed = False
        for a, lab in self.annun.items():
            v = bool(on.get(a))
            if lab.property("on") != v:
                lab.setProperty("on", v)
                changed = True
        if changed:
            self._refresh_annun()

    def set_rate(self, txt):
        self.rate.setText(txt)

    def set_pf(self, pf):
        self.pf.setVisible(bool(pf))
        if pf:
            self.pf.setText(pf)
            if self.pf.property("pf") != pf:
                self.pf.setProperty("pf", pf)
                self.pf.style().unpolish(self.pf)
                self.pf.style().polish(self.pf)
            self.pf.setFont(ui_font(16, QtGui.QFont.Bold))

    def set_extra(self, txt):
        self.extra.setText(txt)

    def set_secondary(self, txt):
        self.sec.setText(txt)

    def set_bar(self, frac, scale_txt=""):
        self.bar.set_fraction(frac if frac is not None else 0.0)
        self.scale.setText(scale_txt)

    def set_statsline(self, txt):
        self.statsline.setText(txt)
