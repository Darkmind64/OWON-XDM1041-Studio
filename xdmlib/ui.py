# -*- coding: utf-8 -*-
"""Composants dessinés : boutons modernes, touches d'instrument, navigation, interrupteurs, sélecteurs, flow layout."""
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt
from . import theme as TH
from .theme import qc, mix, on_color, svg_pixmap, ICONS


def T(k):
    return TH.CURRENT.get(k, "#888888")


def mono(size, bold=False):
    f = QtGui.QFont(TH.CURRENT.get("digit_font", "JetBrains Mono"))
    f.setStyleHint(QtGui.QFont.Monospace)
    f.setPixelSize(int(size))
    f.setBold(bold)
    return f


def ui_font(size, weight=QtGui.QFont.Normal):
    f = QtGui.QFont(TH.UI_FONT)
    f.setPixelSize(int(size))
    f.setWeight(weight)
    return f


def rrect(p, r, radius, fill, border=None, width=1.0, dash=False):
    p.setBrush(qc(fill) if fill else Qt.NoBrush)
    if border:
        pen = QtGui.QPen(qc(border), width)
        if dash:
            pen.setStyle(Qt.DashLine)
        p.setPen(pen)
    else:
        p.setPen(Qt.NoPen)
    p.drawRoundedRect(QtCore.QRectF(r).adjusted(width / 2, width / 2, -width / 2, -width / 2), radius, radius)


class FlowLayout(QtWidgets.QLayout):
    def __init__(self, parent=None, spacing=6):
        super().__init__(parent)
        self.items, self._sp = [], spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, it):
        self.items.append(it)

    def count(self):
        return len(self.items)

    def itemAt(self, i):
        return self.items[i] if 0 <= i < len(self.items) else None

    def takeAt(self, i):
        return self.items.pop(i) if 0 <= i < len(self.items) else None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._do(QtCore.QRect(0, 0, w, 0), True)

    def setGeometry(self, r):
        super().setGeometry(r)
        self._do(r, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QtCore.QSize()
        for it in self.items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _do(self, r, test):
        x, y, lh = r.x(), r.y(), 0
        for it in self.items:
            if it.widget() and not it.widget().isVisible() and not test:
                pass
            hint = it.sizeHint()
            nx = x + hint.width() + self._sp
            if nx - self._sp > r.right() + 1 and lh > 0:
                x, y = r.x(), y + lh + self._sp
                nx, lh = x + hint.width() + self._sp, 0
            if not test:
                it.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), hint))
            x, lh = nx, max(lh, hint.height())
        return y + lh - r.y()


class PaintButton(QtWidgets.QAbstractButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)
        self._hover = False

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)


class TileButton(PaintButton):
    """Tuile de fonction (style Atelier) ou touche de face avant (style Instrument)."""

    def __init__(self, sym, label, sub="", style="tile", parent=None):
        super().__init__(parent)
        self.sym, self.label, self.sub, self.kind = sym, label, sub, style
        self.setCheckable(True)
        self.setToolTip(label)
        self.setMinimumHeight(58 if style == "tile" else 62)

    def sizeHint(self):
        return QtCore.QSize(80, 58 if self.kind == "tile" else 62)

    def minimumSizeHint(self):
        return QtCore.QSize(54, 58 if self.kind == "tile" else 62)

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        on = self.isChecked()
        acc = T("accent")
        r = self.rect()
        if self.kind == "tile":
            bg = T("btn_on") if on else (mix(T("btn"), T("text"), 0.05) if self._hover else T("btn"))
            rrect(p, r, 10, bg, acc if on else T("border"), 1.2 if on else 1)
            p.setPen(qc(acc if on else T("text")))
            p.setFont(mono(17, True))
            p.drawText(QtCore.QRect(0, 6, r.width(), r.height() // 2 + 2), Qt.AlignHCenter | Qt.AlignBottom, self.sym)
            p.setPen(qc(acc if on else T("muted")))
            p.setFont(ui_font(11))
            p.drawText(QtCore.QRect(0, r.height() // 2 + 8, r.width(), r.height() // 2 - 8), Qt.AlignHCenter | Qt.AlignTop, self.label)
        else:
            base = T("surface2")
            bg = mix(base, acc, 0.16) if on else (mix(base, T("text"), 0.06) if self._hover else base)
            rrect(p, r, 10, bg, acc if on else mix(T("border"), T("text"), 0.08), 1.2 if on else 1)
            p.setPen(qc(mix(bg, "#ffffff", 0.07)))
            p.drawLine(QtCore.QPointF(10, 1.5), QtCore.QPointF(r.width() - 10, 1.5))
            p.setPen(qc(acc))
            p.setFont(ui_font(10, QtGui.QFont.DemiBold))
            p.drawText(QtCore.QRect(0, 8, r.width(), 16), Qt.AlignHCenter, self.sub)
            p.setPen(qc(acc if on else T("text")))
            p.setFont(ui_font(16, QtGui.QFont.Bold))
            p.drawText(QtCore.QRect(0, 24, r.width(), r.height() - 30), Qt.AlignCenter, self.sym)


class ActionButton(PaintButton):
    """Bouton d'action moderne : icône + texte + rappel du raccourci clavier."""

    def __init__(self, icon_name, text="", key="", variant="normal", parent=None):
        super().__init__(parent)
        self.icon_name, self.key, self.variant = icon_name, key, variant
        self.setText(text)
        self.setFixedHeight(44)

    def sizeHint(self):
        fm = QtGui.QFontMetrics(ui_font(14, QtGui.QFont.Medium))
        w = 16 + 18 + (10 + fm.horizontalAdvance(self.text()) if self.text() else 0) + (34 if self.key else 0) + 16
        return QtCore.QSize(max(44, w), 44)

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        on, acc = self.isChecked(), T("accent")
        r = self.rect()
        if self.variant == "danger":
            f = T("fail")
            if on:
                bg, bd, fg = f, f, on_color(f)
            else:
                bg, bd, fg = mix(T("bg"), f, 0.13), mix(T("bg"), f, 0.42), mix(f, "#ffffff", 0.55)
        else:
            bg = T("btn_on") if on else (mix(T("btn"), T("text"), 0.05) if self._hover else T("btn"))
            bd = acc if on else (mix(T("border"), T("text"), 0.25) if self._hover else T("border"))
            fg = acc if on else T("btn_text")
        if self.isDown():
            bg = mix(bg, T("text"), 0.08)
        rrect(p, r, 12, bg, bd, 1)
        x = 14 if (self.text() or self.key) else (r.width() - 18) // 2
        if self.variant == "danger" and self.icon_name == "rec":
            p.setBrush(qc(fg if on else f))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QtCore.QPointF(x + 7, r.height() / 2), 5.5, 5.5)
        else:
            p.drawPixmap(x, (r.height() - 18) // 2, svg_pixmap(ICONS.get(self.icon_name, self.icon_name), fg, 18))
        x += 18 + 10
        if self.text():
            p.setPen(qc(fg))
            p.setFont(ui_font(14, QtGui.QFont.DemiBold if self.variant == "danger" else QtGui.QFont.Medium))
            fm = p.fontMetrics()
            p.drawText(QtCore.QRect(x, 0, fm.horizontalAdvance(self.text()) + 2, r.height()), Qt.AlignVCenter, self.text())
            x += fm.horizontalAdvance(self.text()) + 10
        if self.key:
            kr = QtCore.QRect(x, (r.height() - 20) // 2, 24, 20)
            rrect(p, kr, 5, mix(T("bg"), "#000000", 0.2), T("border"), 1)
            p.setPen(qc(T("muted")))
            p.setFont(mono(11))
            p.drawText(kr, Qt.AlignCenter, self.key)


class NavButton(PaintButton):
    def __init__(self, key, icon_name, label, vertical=True, parent=None):
        super().__init__(parent)
        self.key, self.icon_name, self.label, self.vertical = key, icon_name, label, vertical
        self.badge = ""
        self.setCheckable(True)
        self.setAutoExclusive(False)
        self.setToolTip(label)
        if vertical:
            self.setFixedSize(76, 64)
        else:
            self.setFixedHeight(38)

    def sizeHint(self):
        if self.vertical:
            return QtCore.QSize(76, 64)
        fm = QtGui.QFontMetrics(ui_font(14, QtGui.QFont.DemiBold))
        return QtCore.QSize(14 + 16 + 8 + fm.horizontalAdvance(self.label + (" · " + self.badge if self.badge else "")) + 16, 38)

    def set_badge(self, b):
        self.badge = b
        self.updateGeometry()
        self.update()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        on, acc = self.isChecked(), T("accent")
        r = self.rect()
        if on:
            rrect(p, r, 12 if self.vertical else 9, T("btn_on"))
        elif self._hover:
            rrect(p, r, 12 if self.vertical else 9, mix(T("bg"), T("text"), 0.05))
        fg = acc if on else (T("text") if self._hover else T("muted"))
        if self.vertical:
            p.drawPixmap((r.width() - 20) // 2, 12, svg_pixmap(ICONS[self.icon_name], fg, 20, stroke=1.6))
            p.setPen(qc(fg))
            p.setFont(ui_font(11, QtGui.QFont.Medium))
            p.drawText(QtCore.QRect(0, 38, r.width(), 18), Qt.AlignHCenter, self.label)
            if self.badge:
                br = QtCore.QRectF(r.width() - 26, 6, 20, 16)
                rrect(p, br, 8, T("fail"))
                p.setPen(qc(on_color(T("fail"))))
                p.setFont(ui_font(10, QtGui.QFont.Bold))
                p.drawText(br, Qt.AlignCenter, self.badge)
        else:
            p.drawPixmap(14, (r.height() - 16) // 2, svg_pixmap(ICONS[self.icon_name], fg, 16, stroke=1.6))
            p.setPen(qc(fg))
            p.setFont(ui_font(14, QtGui.QFont.DemiBold if on else QtGui.QFont.Normal))
            p.drawText(QtCore.QRect(38, 0, r.width() - 38, r.height()), Qt.AlignVCenter,
                       self.label + (" · " + self.badge if self.badge else ""))


class NavBar(QtWidgets.QWidget):
    changed = QtCore.Signal(str)

    def __init__(self, sections, vertical=True, parent=None):
        super().__init__(parent)
        self.setObjectName("Transparent")
        lay = QtWidgets.QVBoxLayout(self) if vertical else QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6 if vertical else 4)
        self.buttons = {}
        for key, ic, label in sections:
            b = NavButton(key, ic, label, vertical)
            b.clicked.connect(lambda _=False, k=key: self.select(k, emit=True))
            lay.addWidget(b)
            self.buttons[key] = b
        lay.addStretch()

    def select(self, key, emit=False):
        for k, b in self.buttons.items():
            b.setChecked(k == key)
        if emit:
            self.changed.emit(key)

    def set_badge(self, key, text):
        self.buttons[key].set_badge(text)


class Switch(QtWidgets.QCheckBox):
    """Interrupteur (API QCheckBox)."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)

    def sizeHint(self):
        fm = self.fontMetrics()
        return QtCore.QSize(38 + (10 + fm.horizontalAdvance(self.text()) if self.text() else 0), 24)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        on = self.isChecked()
        tr = QtCore.QRectF(0, (self.height() - 22) / 2, 38, 22)
        rrect(p, tr, 11, T("accent") if on else mix(T("border"), T("text"), 0.12))
        k = 18
        kx = tr.right() - k - 2 if on else tr.left() + 2
        p.setBrush(qc(T("bg") if on else T("muted")))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QtCore.QRectF(kx, tr.top() + 2, k, k))
        if self.text():
            p.setPen(qc(T("text")))
            p.drawText(QtCore.QRect(48, 0, self.width() - 48, self.height()), Qt.AlignVCenter, self.text())


class SegmentBox(QtWidgets.QFrame):
    picked = QtCore.Signal(int)

    def __init__(self, labels, parent=None):
        super().__init__(parent)
        self.setObjectName("SegBox")
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.group = QtWidgets.QButtonGroup(self)
        self.group.setExclusive(True)
        self.btns = []
        for i, lab in enumerate(labels):
            b = QtWidgets.QPushButton(lab, checkable=True, objectName="Seg")
            b.setCursor(Qt.PointingHandCursor)
            self.group.addButton(b, i)
            lay.addWidget(b)
            self.btns.append(b)
        self.group.idClicked.connect(self.picked.emit)

    def set_current(self, i):
        if 0 <= i < len(self.btns):
            self.btns[i].setChecked(True)

    def set_label(self, i, text):
        self.btns[i].setText(text)


class StatCard(QtWidgets.QFrame):
    def __init__(self, key, parent=None):
        super().__init__(parent)
        self.setObjectName("StatCard")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(14, 9, 14, 10)
        lay.setSpacing(3)
        k = QtWidgets.QLabel(key.upper(), objectName="StatKey")
        f = ui_font(11, QtGui.QFont.DemiBold)
        f.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, 0.8)
        k.setFont(f)
        self.val = QtWidgets.QLabel("—")
        self.val.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(k)
        lay.addWidget(self.val)
        self.refont()

    def refont(self):
        self.val.setFont(mono(16))


class DockTitle(QtWidgets.QWidget):
    def __init__(self, dock, title):
        super().__init__(dock)
        self.dock = dock
        self.title = title.upper()
        self.setFixedHeight(28)
        self.setCursor(Qt.SizeAllCursor)

    def sizeHint(self):
        return QtCore.QSize(160, 28)

    def minimumSizeHint(self):
        return QtCore.QSize(60, 28)

    def mouseDoubleClickEvent(self, e):
        self.dock.setFloating(not self.dock.isFloating())

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        c = T("sec")
        p.setPen(qc(c))
        p.setFont(ui_font(15, QtGui.QFont.Bold))
        p.drawText(QtCore.QRect(10, 0, 20, self.height()), Qt.AlignVCenter, "⋮⋮")
        f = ui_font(11, QtGui.QFont.DemiBold)
        f.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, 1)
        p.setFont(f)
        p.drawText(QtCore.QRect(34, 0, self.width() - 140, self.height()), Qt.AlignVCenter, self.title)
        g = self.dock.widget().size() if self.dock.widget() else self.size()
        p.setPen(qc(T("muted")))
        p.setFont(mono(11))
        p.drawText(QtCore.QRect(0, 0, self.width() - 10, self.height()), Qt.AlignVCenter | Qt.AlignRight,
                   f"{g.width()} × {g.height()} px" + ("  · flottant" if self.dock.isFloating() else ""))


class PresetButton(PaintButton):
    def __init__(self, name, cells, parent=None):
        super().__init__(parent)
        self.name, self.cells = name, cells
        self.setCheckable(True)
        self.setFixedHeight(88)

    def sizeHint(self):
        return QtCore.QSize(120, 88)

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        on = self.isChecked()
        r = self.rect()
        rrect(p, r, 10, T("btn_on") if on else T("surface"), T("digit") if on else (T("hover") if False else T("border")), 1.2 if on else 1)
        box = QtCore.QRectF(8, 8, r.width() - 16, 52)
        for x, y, w, h, hot in self.cells:
            rr = QtCore.QRectF(box.x() + x * box.width(), box.y() + y * box.height(), w * box.width() - 2, h * box.height() - 2)
            rrect(p, rr, 3, T("digit") if hot else mix(T("border"), T("text"), 0.12))
        p.setPen(qc(T("text")))
        p.setFont(ui_font(12))
        p.drawText(QtCore.QRect(0, 62, r.width(), 22), Qt.AlignCenter, self.name)


class BarGraph(QtWidgets.QWidget):
    def __init__(self, n=50, parent=None):
        super().__init__(parent)
        self.n, self.frac = n, 0.0
        self.setFixedHeight(14)

    def set_fraction(self, f):
        f = max(0.0, min(1.0, f or 0.0))
        if abs(f - self.frac) > 1e-3:
            self.frac = f
            self.update()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        w = self.width()
        gap = 3
        sw = (w - gap * (self.n - 1)) / self.n
        lit = round(self.frac * self.n)
        for i in range(self.n):
            rrect(p, QtCore.QRectF(i * (sw + gap), 0, sw, self.height()), 2, T("digit") if i < lit else T("annun_off"))


class FunctionPad(QtWidgets.QWidget):
    picked = QtCore.Signal(str)
    ITEMS = [("VDC", "V⎓", "V DC", "VOLT DC"), ("VAC", "V~", "V AC", "VOLT AC"), ("IDC", "A⎓", "A DC", "AMP DC"),
             ("IAC", "A~", "A AC", "AMP AC"), ("RES", "Ω", "Résist.", "OHM"), ("CAP", "┤├", "Capa", "CAPA"),
             ("FREQ", "Hz", "Fréq.", "FREQ"), ("PER", "T", "Période", "PERIOD"), ("DIOD", "▷|", "Diode", "DIODE"),
             ("CONT", "•))", "Contin.", "BUZZ"), ("TEMP", "°C", "Temp.", "RTD"), ("AUTO", "AUTO", "Gamme auto", "RANGE")]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Transparent")
        self.grid = QtWidgets.QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(8)
        self.buttons = {}
        self.current = "VDC"
        self.skin = None
        self.set_skin("atelier")

    def set_skin(self, skin):
        if skin == self.skin:
            return
        self.skin = skin
        for b in self.buttons.values():
            b.deleteLater()
        self.buttons = {}
        cols = 3 if skin == "atelier" else 4
        for i, (key, sym, lab, sub) in enumerate(self.ITEMS):
            b = TileButton(sym, lab, sub, "tile" if skin == "atelier" else "key")
            b.setCheckable(key != "AUTO")
            b.clicked.connect(lambda _=False, k=key: self._click(k))
            self.grid.addWidget(b, i // cols, i % cols)
            self.buttons[key] = b
        self.set_current(self.current)

    def _click(self, key):
        if key != "AUTO":
            self.set_current(key)
        self.picked.emit(key)

    def set_current(self, key):
        self.current = key
        for k, b in self.buttons.items():
            b.setChecked(k == key)


class RangeSelector(QtWidgets.QWidget):
    picked = QtCore.Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Transparent")
        self.labels, self.idx, self.skin = [], 0, "atelier"
        self.stack = QtWidgets.QStackedLayout(self)
        self.chips_w = QtWidgets.QWidget(objectName="Transparent")
        self.flow = FlowLayout(self.chips_w, 6)
        self.lcd_w = QtWidgets.QWidget(objectName="Transparent")
        h = QtWidgets.QHBoxLayout(self.lcd_w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        self.prev = QtWidgets.QPushButton("◀")
        self.next = QtWidgets.QPushButton("▶")
        for b in (self.prev, self.next):
            b.setFixedSize(46, 46)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet("border-radius: 23px; font-size: 16px; padding: 0;")
        self.prev.setAccessibleName("Gamme inférieure")
        self.next.setAccessibleName("Gamme supérieure")
        self.lcd = QtWidgets.QLabel(alignment=Qt.AlignCenter, objectName="RangeLcd")
        self.lcd.setFixedHeight(46)
        h.addWidget(self.prev)
        h.addWidget(self.lcd, 1)
        h.addWidget(self.next)
        self.prev.clicked.connect(lambda: self._step(-1))
        self.next.clicked.connect(lambda: self._step(1))
        self.stack.addWidget(self.chips_w)
        self.stack.addWidget(self.lcd_w)
        self.group = QtWidgets.QButtonGroup(self)
        self.group.idClicked.connect(self._pick)

    def set_items(self, labels):
        self.labels = list(labels)
        while self.flow.count():
            it = self.flow.takeAt(0)
            if it.widget():
                self.group.removeButton(it.widget())
                it.widget().deleteLater()
        for i, lab in enumerate(self.labels):
            b = QtWidgets.QPushButton(lab, checkable=True, objectName="Chip")
            b.setCursor(Qt.PointingHandCursor)
            self.group.addButton(b, i)
            self.flow.addWidget(b)
        self.setEnabled(bool(self.labels))
        self.set_current(0)
        self.chips_w.updateGeometry()

    def _pick(self, i):
        self.set_current(i)
        self.picked.emit(i)

    def _step(self, d):
        if not self.labels:
            return
        i = max(0, min(len(self.labels) - 1, self.idx + d))
        if i != self.idx:
            self._pick(i)

    def set_current(self, i):
        self.idx = i
        b = self.group.button(i)
        if b:
            b.setChecked(True)
        self.refresh_lcd()

    def refresh_lcd(self):
        txt = self.labels[self.idx].upper() if self.labels else "—"
        self.lcd.setText(txt)
        self.lcd.setFont(mono(20))
        self.lcd.setStyleSheet(f"background: {T('disp_bg')}; color: {T('digit')}; border: 1px solid {T('disp_border')}; border-radius: 10px;")

    def set_skin(self, skin):
        self.skin = skin
        self.stack.setCurrentIndex(0 if skin == "atelier" else 1)
        self.refresh_lcd()

    def hasHeightForWidth(self):
        return self.skin == "atelier"

    def heightForWidth(self, w):
        return self.flow.heightForWidth(w) if self.skin == "atelier" else 46

    def sizeHint(self):
        return QtCore.QSize(260, self.heightForWidth(260) if self.skin == "atelier" else 46)


def card(title=None, parent=None, margins=(16, 14, 16, 16), spacing=12):
    f = QtWidgets.QFrame(parent, objectName="Card")
    v = QtWidgets.QVBoxLayout(f)
    v.setContentsMargins(*margins)
    v.setSpacing(spacing)
    if title:
        v.addWidget(card_title(title))
    return f, v


def card_title(text):
    lab = QtWidgets.QLabel(text.upper(), objectName="CardTitle")
    f = ui_font(12, QtGui.QFont.DemiBold)
    f.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, 1)
    lab.setFont(f)
    return lab
