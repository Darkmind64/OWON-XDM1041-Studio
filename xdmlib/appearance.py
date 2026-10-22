# -*- coding: utf-8 -*-
"""Dialogue Apparence : thèmes, couleurs, afficheur, style A/B, navigation — aperçu en direct sur toute l'application."""
import copy, math
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt
import pyqtgraph as pg
from . import theme as TH
from .theme import qc, mix, on_color
from .ui import PaintButton, ActionButton, SegmentBox, card_title, ui_font, mono, rrect, T
from .display import DisplayWidget


class PresetCard(PaintButton):
    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.name = name
        self.p = TH.preset(name)
        self.setCheckable(True)
        self.setFixedHeight(96)
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)

    def sizeHint(self):
        return QtCore.QSize(130, 96)

    def minimumSizeHint(self):
        return QtCore.QSize(86, 96)

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        on = self.isChecked()
        r = self.rect()
        rrect(p, r, 12, T("surface2"), self.p["digit"] if on else T("border"), 2 if on else 1)
        inner = QtCore.QRectF(8, 8, r.width() - 16, 56)
        rrect(p, inner, 8, self.p["disp_bg"], self.p["disp_border"], 1)
        p.setPen(qc(self.p["digit"]))
        p.setFont(mono(22, True))
        p.drawText(inner, Qt.AlignCenter, "5.004")
        p.setPen(qc(T("text")))
        p.setFont(ui_font(13, QtGui.QFont.Medium))
        p.drawText(QtCore.QRect(0, 66, r.width(), 26), Qt.AlignCenter, self.name)


class ColorRow(PaintButton):
    changed = QtCore.Signal(str, str)

    def __init__(self, key, label, parent=None):
        super().__init__(parent)
        self.key, self.label, self.color = key, label, "#000000"
        self.setFixedHeight(46)
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        self.clicked.connect(self.pick)

    def sizeHint(self):
        return QtCore.QSize(280, 46)

    def minimumSizeHint(self):
        return QtCore.QSize(190, 46)

    def set_color(self, c):
        self.color = c
        self.update()

    def pick(self):
        c = QtWidgets.QColorDialog.getColor(qc(self.color), self, self.label)
        if c.isValid():
            self.set_color(c.name())
            self.changed.emit(self.key, c.name())

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        r = self.rect()
        rrect(p, r, 10, mix(T("surface2"), T("text"), 0.04 if self._hover else 0), T("border"), 1)
        rrect(p, QtCore.QRectF(12, 10, 26, 26), 7, self.color, mix(T("text"), T("surface"), 0.7), 1)
        p.setPen(qc(T("text")))
        p.setFont(ui_font(13))
        hexw = 90 if r.width() >= 270 else 0
        p.drawText(QtCore.QRect(50, 0, r.width() - 54 - hexw, r.height()), Qt.AlignVCenter, self.label)
        if not hexw:
            return
        p.setPen(qc(T("muted")))
        p.setFont(mono(12))
        p.drawText(QtCore.QRect(0, 0, r.width() - 12, r.height()), Qt.AlignVCenter | Qt.AlignRight, self.color)


class AppearanceDialog(QtWidgets.QDialog):
    def __init__(self, main, theme_dict):
        super().__init__(main)
        self.main = main
        self.setWindowTitle("Apparence")
        scr = QtGui.QGuiApplication.primaryScreen().availableGeometry() if QtGui.QGuiApplication.primaryScreen() else QtCore.QRect(0, 0, 1600, 900)
        self.resize(min(1280, scr.width() - 40), min(800, scr.height() - 60))
        self.setMinimumSize(720, 520)
        self.original = copy.deepcopy(theme_dict)
        self.t = copy.deepcopy(theme_dict)
        self.rows = []
        self.timer = QtCore.QTimer(self, singleShot=True, interval=90, timeout=self._apply_live)

        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        nav = QtWidgets.QFrame(objectName="SidePanel")
        nav.setFixedWidth(180)
        self.navw = nav
        nl = QtWidgets.QVBoxLayout(nav)
        nl.setContentsMargins(12, 20, 12, 20)
        nl.setSpacing(4)
        title = QtWidgets.QLabel("Apparence", objectName="Title")
        title.setFont(ui_font(18, QtGui.QFont.Bold))
        nl.addWidget(title)
        nl.addSpacing(10)
        self.stack = QtWidgets.QStackedWidget()
        self.cat_group = QtWidgets.QButtonGroup(self)
        for i, cat in enumerate(["Thème", "Afficheur", "Graphique", "États", "Interface"]):
            b = QtWidgets.QPushButton(cat, checkable=True, objectName="Seg")
            b.setStyleSheet("text-align: left; padding: 9px 12px;")
            self.cat_group.addButton(b, i)
            nl.addWidget(b)
        nl.addStretch()
        self.cat_group.idClicked.connect(self.stack.setCurrentIndex)
        self.cat_group.button(0).setChecked(True)
        root.addWidget(nav)

        center = QtWidgets.QWidget(objectName="Transparent")
        cl = QtWidgets.QVBoxLayout(center)
        cl.setContentsMargins(26, 22, 26, 20)
        cl.setSpacing(16)
        cl.addWidget(self.stack, 1)
        self._page_theme()
        self._page_display()
        self._page_colors("Graphique", extra=self._curve_width)
        self._page_colors("États")
        self._page_interface()
        bb = QtWidgets.QHBoxLayout()
        bb.setSpacing(10)
        for txt, fn in (("Importer…", self.import_theme), ("Exporter le thème", self.export_theme)):
            b = QtWidgets.QPushButton(txt)
            b.clicked.connect(fn)
            bb.addWidget(b)
        bb.addStretch()
        cancel = QtWidgets.QPushButton("Annuler")
        cancel.clicked.connect(self.reject)
        ok = QtWidgets.QPushButton("Appliquer", objectName="Primary")
        ok.clicked.connect(self.accept)
        bb.addWidget(cancel)
        bb.addWidget(ok)
        cl.addLayout(bb)
        root.addWidget(center, 1)

        aside = QtWidgets.QFrame(objectName="SidePanel")
        aside.setFixedWidth(360)
        self.aside = aside
        al = QtWidgets.QVBoxLayout(aside)
        al.setContentsMargins(20, 22, 20, 20)
        al.setSpacing(14)
        al.addWidget(card_title("Aperçu en direct"))
        self.prev_disp = DisplayWidget(preview=True)
        self.prev_disp.setMinimumHeight(200)
        self.prev_disp.set_value("5.00398", "V")
        self.prev_disp.set_chips(["V DC", "Auto · 5 V", "Rapide"])
        self.prev_disp.set_pf("PASS")
        self.prev_disp.set_secondary("50.0012 Hz")
        self.prev_disp.set_annunciators({"AUTO": 1, "AVG": 1, "RMT": 1})
        self.prev_disp.set_bar(0.8, "5 V")
        self.prev_disp.set_statsline("MIN 4.98902  MAX 5.01342")
        al.addWidget(self.prev_disp)
        self.prev_plot = pg.PlotWidget()
        self.prev_plot.setMinimumHeight(180)
        self.prev_plot.hideAxis("bottom")
        self.prev_plot.hideAxis("left")
        self.prev_plot.setMouseEnabled(False, False)
        self.prev_plot.showGrid(x=True, y=True, alpha=0.6)
        xs = list(range(160))
        self.prev_curve = self.prev_plot.plot(xs, [math.sin(i / 14) + 0.06 * math.sin(i * 1.7) for i in xs])
        self.prev_lim = [pg.InfiniteLine(angle=0, pos=1.25), pg.InfiniteLine(angle=0, pos=-1.25)]
        for ln in self.prev_lim:
            self.prev_plot.addItem(ln)
        al.addWidget(self.prev_plot)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(ActionButton("hold", "HOLD", "H"))
        rel = ActionButton("rel", "REL Δ", "R")
        rel.setCheckable(True)
        rel.setChecked(True)
        row.addWidget(rel)
        al.addLayout(row)
        al.addStretch()
        root.addWidget(aside)
        self.sync_controls()
        self._refresh_preview()

    # ------------------------------------------------------------ pages
    def _scroll_page(self):
        w = QtWidgets.QWidget(objectName="Transparent")
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(0, 0, 8, 0)
        v.setSpacing(16)
        sa = QtWidgets.QScrollArea(widgetResizable=True)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sa.setWidget(w)
        self.stack.addWidget(sa)
        return v

    def _color_grid(self, v, group):
        g = QtWidgets.QGridLayout()
        g.setHorizontalSpacing(20)
        g.setVerticalSpacing(10)
        for i, (k, lab) in enumerate(TH.COLOR_GROUPS[group]):
            r = ColorRow(k, lab)
            r.changed.connect(self.set_key)
            self.rows.append(r)
            g.addWidget(r, i // 2, i % 2)
        v.addLayout(g)

    def _page_theme(self):
        v = self._scroll_page()
        v.addWidget(card_title("Thèmes prêts à l’emploi"))
        g = QtWidgets.QHBoxLayout()
        g.setSpacing(10)
        self.presets = {}
        for name in TH.PRESETS:
            c = PresetCard(name)
            c.clicked.connect(lambda _=False, n=name: self.pick_preset(n))
            self.presets[name] = c
            g.addWidget(c)
        v.addLayout(g)
        v.addWidget(card_title("Couleurs principales"))
        self._color_grid(v, "Thème")
        v.addStretch()

    def _page_display(self):
        v = self._scroll_page()
        v.addWidget(card_title("Couleurs de l’afficheur"))
        self._color_grid(v, "Afficheur")
        h = QtWidgets.QHBoxLayout()
        h.setSpacing(24)
        fbox = QtWidgets.QVBoxLayout()
        fbox.addWidget(QtWidgets.QLabel("Police des chiffres", objectName="Muted"))
        self.font_group = QtWidgets.QButtonGroup(self)
        for i, f in enumerate(TH.available_digit_fonts()):
            b = QtWidgets.QPushButton(f + ("  (LCD)" if "Share" in f else ""), checkable=True)
            b.setProperty("font_name", f)
            b.setStyleSheet("text-align: left;")
            b.setFont(QtGui.QFont(f, 11))
            self.font_group.addButton(b, i)
            fbox.addWidget(b)
        self.font_group.buttonClicked.connect(lambda b: self.set_key("digit_font", b.property("font_name")))
        fbox.addStretch()
        h.addLayout(fbox, 1)
        sbox = QtWidgets.QVBoxLayout()
        self.lb_size = QtWidgets.QLabel("", objectName="Muted")
        self.sl_size = QtWidgets.QSlider(Qt.Horizontal, minimum=48, maximum=220)
        self.sl_size.valueChanged.connect(lambda x: (self.set_key("digit_size", x), self.lb_size.setText(f"Taille des chiffres · {x} px")))
        self.lb_glow = QtWidgets.QLabel("", objectName="Muted")
        self.sl_glow = QtWidgets.QSlider(Qt.Horizontal, minimum=0, maximum=100)
        self.sl_glow.valueChanged.connect(lambda x: (self.set_key("glow", x), self.lb_glow.setText(f"Lueur de l’afficheur · {x} %")))
        for w in (self.lb_size, self.sl_size, self.lb_glow, self.sl_glow):
            sbox.addWidget(w)
        sbox.addStretch()
        h.addLayout(sbox, 1)
        obox = QtWidgets.QVBoxLayout()
        obox.addWidget(QtWidgets.QLabel("Options", objectName="Muted"))
        self.ck_pf = QtWidgets.QCheckBox("Couleur des chiffres selon PASS / FAIL")
        self.ck_blink = QtWidgets.QCheckBox("Clignoter en dépassement (OL)")
        self.ck_bar = QtWidgets.QCheckBox("Barregraphe sous la valeur (style Atelier)")
        for ck, k in ((self.ck_pf, "pf_color"), (self.ck_blink, "blink_ol"), (self.ck_bar, "bargraph")):
            ck.toggled.connect(lambda on, k=k: self.set_key(k, on))
            obox.addWidget(ck)
        obox.addStretch()
        h.addLayout(obox, 1)
        v.addLayout(h)
        v.addStretch()

    def _curve_width(self, v):
        self.lb_cw = QtWidgets.QLabel("", objectName="Muted")
        self.sl_cw = QtWidgets.QSlider(Qt.Horizontal, minimum=5, maximum=40)
        self.sl_cw.valueChanged.connect(lambda x: (self.set_key("curve_width", x / 10), self.lb_cw.setText(f"Épaisseur de la courbe · {x / 10:.1f} px")))
        v.addWidget(self.lb_cw)
        v.addWidget(self.sl_cw)

    def _page_colors(self, group, extra=None):
        v = self._scroll_page()
        v.addWidget(card_title(group))
        self._color_grid(v, group)
        if extra:
            extra(v)
        v.addStretch()

    def _page_interface(self):
        v = self._scroll_page()
        v.addWidget(card_title("Style de l’interface"))
        self.seg_skin = SegmentBox(["A — Atelier (moderne)", "B — Instrument (face avant)"])
        self.seg_skin.picked.connect(lambda i: self.set_key("skin", ["atelier", "instrument"][i]))
        v.addWidget(self.seg_skin)
        v.addWidget(card_title("Navigation"))
        self.seg_nav = SegmentBox(["Barre latérale", "Onglets en haut"])
        self.seg_nav.picked.connect(lambda i: self.set_key("nav", ["rail", "tabs"][i]))
        v.addWidget(self.seg_nav)
        v.addWidget(card_title("Couleurs de l’interface"))
        self._color_grid(v, "Interface")
        v.addStretch()

    # ------------------------------------------------------------ logique
    def sync_controls(self):
        t = self.t
        for r in self.rows:
            r.set_color(t[r.key])
        for n, c in self.presets.items():
            c.setChecked(t.get("name") == n)
        for b in self.font_group.buttons():
            b.setChecked(b.property("font_name") == t["digit_font"])
        for w, val in ((self.sl_size, t["digit_size"]), (self.sl_glow, t["glow"]), (self.sl_cw, int(t["curve_width"] * 10))):
            w.blockSignals(True)
            w.setValue(int(val))
            w.blockSignals(False)
        self.lb_size.setText(f"Taille des chiffres · {t['digit_size']} px")
        self.lb_glow.setText(f"Lueur de l’afficheur · {t['glow']} %")
        self.lb_cw.setText(f"Épaisseur de la courbe · {t['curve_width']:.1f} px")
        for ck, k in ((self.ck_pf, "pf_color"), (self.ck_blink, "blink_ol"), (self.ck_bar, "bargraph")):
            ck.blockSignals(True)
            ck.setChecked(bool(t[k]))
            ck.blockSignals(False)
        self.seg_skin.set_current(0 if t["skin"] == "atelier" else 1)
        self.seg_nav.set_current(0 if t["nav"] == "rail" else 1)

    def pick_preset(self, name):
        keep = {k: self.t[k] for k in TH.OPTIONS}
        self.t = TH.preset(name)
        self.t.update(keep)
        self.sync_controls()
        self.timer.start()

    def set_key(self, k, v):
        self.t[k] = v
        if k in TH.PRESETS["Phosphore"]:
            self.t["name"] = "Personnalisé"
            for c in self.presets.values():
                c.setChecked(False)
        for r in self.rows:
            if r.key == k:
                r.set_color(v)
        self.timer.start()

    def _apply_live(self):
        self.main.apply_theme(self.t, save=False)
        self._refresh_preview()
        self.update()

    def _refresh_preview(self):
        t = TH.CURRENT
        self.prev_disp.apply_theme(t["skin"])
        self.prev_plot.setBackground(t["plot"])
        self.prev_curve.setPen(pg.mkPen(t["curve"], width=t["curve_width"]))
        for ln in self.prev_lim:
            ln.setPen(pg.mkPen(t["fail"], style=Qt.DashLine))
        for ax in ("left", "bottom"):
            self.prev_plot.getAxis(ax).setPen(pg.mkPen(mix(t["grid"], t["muted"], 0.5)))

    def accept(self):
        self.main.apply_theme(self.t, save=True)
        super().accept()

    def reject(self):
        self.main.apply_theme(self.original, save=False)
        super().reject()

    def export_theme(self):
        fn, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Exporter le thème", f"theme_{self.t.get('name', 'perso')}.json", "Thème (*.json)")
        if fn:
            with open(fn, "w", encoding="utf-8") as f:
                f.write(TH.to_json(self.t))

    def import_theme(self):
        fn, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Importer un thème", "", "Thème (*.json)")
        if not fn:
            return
        try:
            with open(fn, encoding="utf-8") as f:
                self.t = TH.from_json(f.read())
        except (OSError, ValueError) as e:
            QtWidgets.QMessageBox.warning(self, "Apparence", f"Thème illisible : {e}")
            return
        self.sync_controls()
        self.timer.start()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "aside"):
            self.aside.setVisible(self.width() >= 1080)
