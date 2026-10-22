# -*- coding: utf-8 -*-
"""OWON XDM1041 Studio — logiciel PC pour multimètre OWON XDM1041 (SCPI sur port COM USB)."""
import sys, os, io, time, math, csv, json, datetime
from collections import deque

import numpy as np
import serial, serial.tools.list_ports
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt
import pyqtgraph as pg
import pyqtgraph.exporters

from xdmlib.core import (APP, MODES, FUNC_MAP, DB_REFS, fmt_si, fmt_parts, auto_full_scale, speak_si, try_si, Stats, DmmWorker)
from xdmlib.analysis import SERIES, nearest_e, auto_threshold, consumption, AnomalyDetector, Battery
from xdmlib.services import WebServer, MqttPublisher, Speaker
from xdmlib.reports import build_pdf, table_html, export_xlsx
from xdmlib.sequence import SeqRunner, EXAMPLE, HELP
from xdmlib.widgets import app_icon, FloatingDisplay
from xdmlib import theme as TH
from xdmlib.theme import mix
from xdmlib.ui import (ActionButton, NavBar, Switch, SegmentBox, StatCard, DockTitle, PresetButton, FunctionPad,
                       RangeSelector, card, card_title, ui_font, mono)
from xdmlib.display import DisplayWidget
from xdmlib.appearance import AppearanceDialog
from xdmlib.updater import Updater

try:
    import winsound
except ImportError:
    winsound = None

VERSION = "3.0"
MAXBUF = 300_000
TRACE_COLORS = ["#4cff7a", "#ffb74c", "#7ad7ff", "#ff6fb5", "#c8a2ff", "#ffe066", "#ff5050", "#5cf2d6"]
SECTIONS = [("mesure", "mesure", "Mesure"), ("analyse", "analyse", "Analyse"), ("tri", "tri", "Tri"),
            ("batt", "batterie", "Batterie"), ("conso", "conso", "Conso"), ("seq", "sequences", "Séquences"),
            ("journal", "journal", "Journal"), ("reglages", "reglages", "Réglages")]
DOCKS = [("display", "Afficheur"), ("actions", "Actions"), ("stats", "Statistiques"), ("function", "Fonction · Gamme"),
         ("limits", "Limites"), ("chart", "Graphique")]
LAYOUTS = {
    "Atelier": [(0, 0, .27, .64, 0), (0, .68, .27, .32, 0), (.30, 0, .70, .34, 1), (.30, .38, .70, .10, 0), (.30, .52, .70, .12, 0), (.30, .68, .70, .32, 0)],
    "Grand afficheur": [(0, 0, 1, .55, 1), (0, .59, 1, .10, 0), (0, .73, 1, .27, 0)],
    "Analyse": [(0, 0, .45, .30, 1), (.48, 0, .52, .30, 0), (0, .34, 1, .66, 0)],
    "Compact": [(0, 0, 1, .78, 1), (0, .82, 1, .18, 0)],
}
LAYOUT_VERSION = 3
DEFAULT_PROFILES = {
    "Tri résistances 1 %": dict(mode="RES", range=0, speed=1, ah_en=True, ah_n=5, ah_tol=0.2, ah_min="0.1",
                                bin_en=True, bin_series=2, bin_tol=1.0, lim_en=False, avg=1, interval=0),
    "Test batterie 12 V": dict(mode="VDC", range=0, speed=1, interval=1000, bat_load=0, bat_val="1", bat_cut="10.5",
                               lim_en=False, avg=1, ah_en=False),
    "Fuite / veille (µA)": dict(mode="IDC", range=0, speed=0, avg=1, interval=0, cons_thr="1m", cons_cap="2000",
                                ah_en=False, lim_en=False),
    "Tension secteur": dict(mode="VAC", range=0, speed=1, dual=True, interval=500, ah_en=False, lim_en=False),
    "Diodes / LED": dict(mode="DIOD", speed=2, ah_en=True, ah_n=4, ah_tol=0.5, ah_min="0.1", lim_en=False, interval=0),
}


def data_dir():
    p = QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.AppDataLocation)
    os.makedirs(p, exist_ok=True)
    return p


class StatsPanel(QtWidgets.QWidget):
    """Cartes de statistiques qui se réorganisent selon la largeur."""

    def __init__(self, keys, parent=None):
        super().__init__(parent)
        self.setObjectName("Transparent")
        self.grid = QtWidgets.QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(10)
        self.cards = {k: StatCard(k) for k in keys}
        self.cols = 0
        self._relayout(len(keys))

    def _relayout(self, cols):
        if cols == self.cols:
            return
        self.cols = cols
        for c in self.cards.values():
            self.grid.removeWidget(c)
        for i, c in enumerate(self.cards.values()):
            self.grid.addWidget(c, i // cols, i % cols)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relayout(max(1, min(len(self.cards), self.width() // 128)))


class Main(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP} {VERSION}")
        self.setWindowIcon(app_icon())
        self.resize(1500, 960)
        self.cfg = QtCore.QSettings("Davy", "XDM1041Studio")
        try:
            self.theme_t = TH.from_json(self.cfg.value("theme_json")) if self.cfg.value("theme_json") else TH.preset("Phosphore")
        except (ValueError, TypeError):
            self.theme_t = TH.preset("Phosphore")
        TH.apply(QtWidgets.QApplication.instance(), self.theme_t)
        pg.setConfigOptions(antialias=True, background=self.theme_t["plot"], foreground=self.theme_t["muted"])
        self.worker = None
        self.mode = "VDC"
        self.unit_override = None
        self.hold = False
        self.rel = None
        self.last_v = self.last_s = None
        self.last_t = 0.0
        self.idn = ""
        self.disp_text = "-----"
        self.disp_color = None
        self.tbuf, self.ybuf = deque(maxlen=MAXBUF), deque(maxlen=MAXBUF)
        self.view_t = self.view_y = np.array([])
        self.stats = Stats()
        self.filt = deque(maxlen=1)
        self.rate_ts = deque(maxlen=400)
        self.integ, self.integ_t = 0.0, None
        self.pf, self.npass, self.nfail = None, 0, 0
        self.ah_buf, self.ah_armed, self.ah_cap = deque(maxlen=5), True, None
        self.rec_file = self.rec_writer = None
        self.rec_state, self.rec_prev, self.rec_target = None, None, None
        self.rec_count, self.rec_t0, self.rec_n = 0, 0.0, 0
        self.lo = self.hi = None
        self.bins = {}
        self.anom = AnomalyDetector()
        self.battery = Battery()
        self.want_rate, self.rate_retry = None, False
        self.slow_letter = self.cfg.value("slow_letter", "S")
        self.tts_last = 0.0
        self.first_connect = True
        self.ignore_until = 0.0
        self.persist = {}
        self.plots = []
        self.traces = []
        self.section = "mesure"
        self.edit_mode = False
        self.compact_state = None
        self.n_events = 0
        self.web = WebServer()
        self.mqtt = MqttPublisher()
        self.speaker = Speaker()
        self.floating = None
        self.seq = SeqRunner(self)
        self._build()
        self.mqtt.status.connect(lambda s: self.lb_mqtt.setText(s))
        self.seq.log.connect(self.seq_out.appendPlainText)
        self.seq.done.connect(self.seq_done)
        self._shortcuts()
        self._load_settings()
        self.profiles = self._load_profiles()
        self._refresh_profile_menu()
        self.refresh_ports()
        st = self.cfg.value("last_state")
        if st:
            try:
                self.set_state(json.loads(st), device=False)
            except (ValueError, TypeError):
                self.set_mode("VDC", send=False)
        else:
            self.set_mode("VDC", send=False)
        self.apply_layout("Atelier")
        ls = self.cfg.value("layout_state")
        if ls:
            self.dockhost.restoreState(QtCore.QByteArray.fromBase64(ls.encode()), LAYOUT_VERSION)
        self.apply_theme(self.theme_t, save=False)
        self.goto("mesure")
        self.timer = QtCore.QTimer(self, interval=150, timeout=self.refresh_views)
        self.timer.start()
        self.updater = Updater(self, VERSION)
        if self.act_autoupd.isChecked():
            QtCore.QTimer.singleShot(4000, self.updater.auto_check)

    # ================================================================== construction UI
    def R(self, name, w):
        self.persist[name] = w
        return w

    def _plot(self, **kw):
        p = pg.PlotWidget(**kw)
        p.showGrid(x=True, y=True, alpha=0.35)
        self.plots.append(p)
        return p

    def _build(self):
        self._menus()
        central = QtWidgets.QWidget(objectName="Root")
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._header())
        self.tabsrow = QtWidgets.QFrame(objectName="TabsRow")
        tr = QtWidgets.QHBoxLayout(self.tabsrow)
        tr.setContentsMargins(14, 6, 14, 6)
        self.nav_tabs = NavBar(SECTIONS, vertical=False)
        self.nav_tabs.changed.connect(self.goto)
        tr.addWidget(self.nav_tabs)
        tr.addStretch()
        root.addWidget(self.tabsrow)
        body = QtWidgets.QHBoxLayout()
        body.setContentsMargins(16, 16, 16, 12)
        body.setSpacing(16)
        self.nav_rail = NavBar(SECTIONS, vertical=True)
        self.nav_rail.changed.connect(self.goto)
        self.nav_rail.setFixedWidth(76)
        body.addWidget(self.nav_rail)
        self.stack = QtWidgets.QStackedWidget()
        body.addWidget(self.stack, 1)
        root.addLayout(body, 1)
        self.pages = {}
        for key, builder in (("mesure", self._page_mesure), ("analyse", self._page_analyse), ("tri", self._page_tri),
                             ("batt", self._page_batt), ("conso", self._page_conso), ("seq", self._page_seq),
                             ("journal", self._page_journal), ("reglages", self._page_reglages)):
            w = builder()
            self.pages[key] = self.stack.addWidget(w)
        self.statusBar().showMessage("Prêt")

    def _header(self):
        h = QtWidgets.QFrame(objectName="Header")
        h.setFixedHeight(60)
        lay = QtWidgets.QHBoxLayout(h)
        lay.setContentsMargins(18, 0, 14, 0)
        lay.setSpacing(10)
        self.logo = QtWidgets.QLabel()
        lay.addWidget(self.logo)
        t = QtWidgets.QLabel("XDM1041 Studio", objectName="Title")
        t.setFont(ui_font(16, QtGui.QFont.Bold))
        lay.addWidget(t)
        lay.addSpacing(14)
        self.lb_idn = QtWidgets.QLabel("Déconnecté", objectName="Pill")
        lay.addWidget(self.lb_idn)
        lay.addStretch()
        self.cb_port = QtWidgets.QComboBox(minimumWidth=210)
        self.cb_port.setMaximumWidth(280)
        self.bt_refresh = QtWidgets.QToolButton(clicked=self.refresh_ports, toolTip="Rafraîchir les ports")
        self.bt_detect = QtWidgets.QPushButton("Détecter", clicked=self.detect_port)
        self.bt_conn = QtWidgets.QPushButton("Connecter", clicked=self.toggle_connect, objectName="Primary", minimumWidth=110)
        for w in (self.cb_port, self.bt_refresh, self.bt_detect, self.bt_conn):
            lay.addWidget(w)
        lay.addSpacing(12)
        lay.addWidget(QtWidgets.QLabel("Profil", objectName="Muted"))
        self.cb_profile = QtWidgets.QComboBox(minimumWidth=180)
        self.cb_profile.activated.connect(lambda i: self.load_profile(self.cb_profile.itemText(i)) if i > 0 else None)
        lay.addWidget(self.cb_profile)
        self.bt_layout = QtWidgets.QPushButton("Disposition", checkable=True, toolTip="Mode disposition : déplacer et redimensionner les panneaux")
        self.bt_layout.toggled.connect(self.set_edit_mode)
        self.bt_theme = QtWidgets.QToolButton(clicked=self.open_appearance, toolTip="Apparence")
        self.bt_menu = QtWidgets.QToolButton(toolTip="Menu")
        self.bt_menu.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.bt_menu.setMenu(self.main_menu)
        for w in (self.bt_layout, self.bt_theme, self.bt_menu):
            lay.addWidget(w)
        return h

    def _menus(self):
        self.main_menu = QtWidgets.QMenu(self)
        mf = self.main_menu.addMenu("Fichier")
        self.menu_prof = mf.addMenu("Profils")
        mf.addSeparator()
        for txt, fn, sc in (("Exporter les données (CSV)…", self.export_buffer, "Ctrl+E"),
                            ("Exporter vers Excel (.xlsx)…", self.export_excel, "Ctrl+Shift+E"),
                            ("Exporter le graphique (PNG)…", self.export_png, None),
                            ("Rapport d'intervention (PDF)…", self.report_pdf, "Ctrl+P")):
            a = mf.addAction(txt, fn)
            if sc:
                a.setShortcut(sc)
                self.addAction(a)
        mf.addSeparator()
        a = mf.addAction("Quitter", self.close)
        a.setShortcut("Ctrl+Q")
        self.addAction(a)
        mv = self.main_menu.addMenu("Affichage")
        mv.addAction("Apparence…", self.open_appearance)
        ms = mv.addMenu("Style")
        self.skin_group = QtGui.QActionGroup(self)
        for k, lab in (("atelier", "A — Atelier (moderne)"), ("instrument", "B — Instrument (face avant)")):
            a = ms.addAction(lab, lambda k=k: self.set_option("skin", k))
            a.setCheckable(True)
            a.setData(k)
            self.skin_group.addAction(a)
        mn = mv.addMenu("Navigation")
        self.nav_group = QtGui.QActionGroup(self)
        for k, lab in (("rail", "Barre latérale"), ("tabs", "Onglets en haut")):
            a = mn.addAction(lab, lambda k=k: self.set_option("nav", k))
            a.setCheckable(True)
            a.setData(k)
            self.nav_group.addAction(a)
        ml = mv.addMenu("Disposition")
        self.menu_layouts = ml
        mv.addSeparator()
        self.act_top = mv.addAction("Toujours au premier plan", self.toggle_top)
        self.act_top.setCheckable(True)
        self.act_compact = mv.addAction("Mode compact", self.toggle_compact)
        self.act_compact.setCheckable(True)
        self.act_float = mv.addAction("Afficheur flottant", self.toggle_floating)
        self.act_float.setCheckable(True)
        self.act_float.setShortcut("F11")
        self.addAction(self.act_float)
        mz = mv.addMenu("Taille du texte")
        self.zoom_group = QtGui.QActionGroup(self)
        for pct in (100, 115, 130, 150, 175):
            a = mz.addAction(f"{pct} %", lambda p=pct: self.set_zoom(p))
            a.setCheckable(True)
            a.setData(pct)
            self.zoom_group.addAction(a)
        mo = self.main_menu.addMenu("Options")
        self.act_autorec = mo.addAction("Reconnexion automatique")
        self.act_fast = mo.addAction("Vitesse rapide à la connexion")
        self.act_restore = mo.addAction("Appliquer le dernier mode à la connexion")
        self.act_rangecmd = mo.addAction("Changer de gamme par RANGE (sans réinitialiser)")
        for a in (self.act_autorec, self.act_fast, self.act_restore, self.act_rangecmd):
            a.setCheckable(True)
            a.setChecked(True)
        self.act_autoupd = mo.addAction("Vérifier les mises à jour au démarrage")
        self.act_autoupd.setCheckable(True)
        self.act_autoupd.setChecked(True)
        mo.addSeparator()
        mo.addAction("Configuration MQTT…", self.mqtt_dialog)
        mh = self.main_menu.addMenu("Aide")
        mh.addAction("Raccourcis clavier", lambda: QtWidgets.QMessageBox.information(self, "Raccourcis", (
            "H  HOLD\nR  REL Δ\nEspace  capture\nP  pause / reprise acquisition\nC  copier la valeur\n"
            "A  auto-échelle du graphique\nF11  afficheur flottant\nCtrl+E  export CSV\nCtrl+Shift+E  export Excel\n"
            "Ctrl+P  rapport PDF\nCtrl+Q  quitter\n\nMode disposition : glisser un panneau par sa poignée ⋮⋮,\n"
            "tirer les séparations pour redimensionner, double-clic sur un titre pour détacher.")))
        mh.addAction("Langage des séquences", lambda: QtWidgets.QMessageBox.information(self, "Séquences", HELP))
        mh.addAction("Rechercher des mises à jour…", lambda: self.updater.check(silent=False))
        mh.addAction("À propos", lambda: QtWidgets.QMessageBox.about(
            self, APP, f"{APP} {VERSION}\nSCPI OWON XDM1041 — USB COM 115200 8N1\nPython / PySide6 / pyqtgraph"))

    # ------------------------------------------------------------ page Mesure (docks)
    def _page_mesure(self):
        page = QtWidgets.QWidget(objectName="Page")
        v = QtWidgets.QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)
        self.banner = QtWidgets.QFrame(objectName="Banner")
        bl = QtWidgets.QHBoxLayout(self.banner)
        bl.setContentsMargins(16, 8, 12, 8)
        bl.setSpacing(12)
        self.banner_icon = QtWidgets.QLabel()
        bt = QtWidgets.QLabel("Mode disposition", objectName="Title")
        bt.setFont(ui_font(15, QtGui.QFont.DemiBold))
        bh = QtWidgets.QLabel("Glissez un panneau par sa poignée ⋮⋮ · tirez les séparations pour redimensionner · double-clic sur un titre pour le détacher",
                              objectName="BannerText")
        bh.setWordWrap(True)
        for w in (self.banner_icon, bt):
            bl.addWidget(w)
        bl.addWidget(bh, 1)
        for txt, fn, name in (("Réinitialiser", lambda: self.apply_layout("Atelier"), "Ghost"), ("Annuler", self.cancel_edit, "Ghost"),
                              ("Enregistrer la disposition", self.save_edit, "Info")):
            b = QtWidgets.QPushButton(txt, objectName=name)
            b.clicked.connect(fn)
            bl.addWidget(b)
        self.banner.hide()
        v.addWidget(self.banner)
        hb = QtWidgets.QHBoxLayout()
        hb.setSpacing(12)
        self.dockhost = QtWidgets.QMainWindow()
        self.dockhost.setWindowFlags(Qt.Widget)
        self.dockhost.setDockNestingEnabled(True)
        self.dockhost.setDockOptions(QtWidgets.QMainWindow.AnimatedDocks | QtWidgets.QMainWindow.AllowNestedDocks |
                                     QtWidgets.QMainWindow.AllowTabbedDocks)
        self.docks, self.dock_titles = {}, {}
        widgets = {"display": self._w_display(), "actions": self._w_actions(), "stats": self._w_stats(),
                   "function": self._w_function(), "limits": self._w_limits(), "chart": self._w_chart()}
        for key, title in DOCKS:
            d = QtWidgets.QDockWidget(title, self.dockhost)
            d.setObjectName("dock_" + key)
            d.setWidget(widgets[key])
            d.setTitleBarWidget(QtWidgets.QWidget())
            d.setFeatures(QtWidgets.QDockWidget.NoDockWidgetFeatures)
            d.topLevelChanged.connect(lambda _fl, k=key: self._dock_title(k))
            d.visibilityChanged.connect(lambda _v, k=key: self._sync_dock_check(k))
            self.docks[key] = d
            self.dock_titles[key] = DockTitle(d, title)
            self.dock_titles[key].hide()
        hb.addWidget(self.dockhost, 1)
        hb.addWidget(self._side_panel())
        v.addLayout(hb, 1)
        self.title_timer = QtCore.QTimer(self, interval=250, timeout=lambda: [t.update() for t in self.dock_titles.values()])
        return page

    def _w_display(self):
        self.disp = DisplayWidget()
        self.disp.setMinimumHeight(150)
        return self.disp

    def _w_actions(self):
        w = QtWidgets.QWidget(objectName="Transparent")
        h = QtWidgets.QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        self.bt_hold = ActionButton("hold", "HOLD", "H")
        self.bt_hold.setCheckable(True)
        self.bt_hold.toggled.connect(lambda on: setattr(self, "hold", on))
        self.bt_rel = ActionButton("rel", "REL Δ", "R")
        self.bt_rel.setCheckable(True)
        self.bt_rel.toggled.connect(self.toggle_rel)
        self.bt_snap = ActionButton("capture", "Capturer", "␣")
        self.bt_snap.clicked.connect(lambda: self.capture(self.last_v))
        self.bt_copy = ActionButton("copy", "Copier", "C")
        self.bt_copy.clicked.connect(self.copy_value)
        self.bt_clear = ActionButton("trash", "")
        self.bt_clear.setToolTip("Effacer données et statistiques")
        self.bt_clear.clicked.connect(self.clear_data)
        self.act_rec = ActionButton("rec", "Enregistrer", "", variant="danger")
        self.act_rec.setCheckable(True)
        self.act_rec.clicked.connect(self._rec_clicked)
        self.bt_run = ActionButton("pause", "")
        self.bt_run.setCheckable(True)
        self.bt_run.setToolTip("Pause / reprise de l'acquisition (P)")
        self.bt_run.toggled.connect(self.toggle_run)
        for b in (self.bt_hold, self.bt_rel, self.bt_snap, self.bt_copy, self.bt_clear):
            h.addWidget(b)
        h.addStretch()
        h.addWidget(self.act_rec)
        h.addWidget(self.bt_run)
        w.setFixedHeight(46)
        return w

    def _w_stats(self):
        self.statsp = StatsPanel(["N", "Min", "Max", "Moyenne", "σ", "Crête-crête", "σ / moy"])
        self.st = {k: c.val for k, c in self.statsp.cards.items()}
        self.statsp.setMinimumHeight(64)
        self.statsp.setMaximumHeight(240)
        return self.statsp

    def _w_function(self):
        f, v = card("Fonction")
        self.func_card = f
        self.fpad = FunctionPad()
        self.fpad.picked.connect(self.on_func_pick)
        v.addWidget(self.fpad)
        v.addWidget(QtWidgets.QLabel("Gamme", objectName="Muted"))
        self.range_sel = RangeSelector()
        self.range_sel.picked.connect(self.on_range_pick)
        v.addWidget(self.range_sel)
        v.addWidget(QtWidgets.QLabel("Vitesse", objectName="Muted"))
        self.speed_seg = SegmentBox(["Lente", "Moyenne", "Rapide"])
        self.speed_seg.picked.connect(self.on_speed_pick)
        v.addWidget(self.speed_seg)
        self.ck_dual = self.R("dual", QtWidgets.QCheckBox("2ᵉ affichage : fréquence (AC)"))
        self.ck_dual.toggled.connect(self.set_dual)
        v.addWidget(self.ck_dual)
        v.addStretch()
        # combos logiques (non affichés, mémorisés dans les profils)
        self.cb_range = self.R("range", QtWidgets.QComboBox(f))
        self.cb_speed = self.R("speed", QtWidgets.QComboBox(f))
        self.cb_speed.addItems(["Lente (S)", "Moyenne (M)", "Rapide (F)"])
        self.cb_range.hide()
        self.cb_speed.hide()
        sa = QtWidgets.QScrollArea(widgetResizable=True)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sa.setWidget(f)
        sa.setMinimumWidth(260)
        return sa

    def _w_limits(self):
        f, v = card(None)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(card_title("Limites"))
        top.addStretch()
        self.ck_lim = self.R("lim_en", Switch())
        self.ck_lim.toggled.connect(self.update_limits)
        top.addWidget(self.ck_lim)
        v.addLayout(top)
        g = QtWidgets.QGridLayout()
        g.setSpacing(8)
        self.ed_nom = self.R("nom", QtWidgets.QLineEdit(placeholderText="ex. 4.7k"))
        self.ed_tol = self.R("tol", QtWidgets.QLineEdit("1"))
        self.ed_lo = self.R("lim_lo", QtWidgets.QLineEdit(placeholderText="ex. 4.65k"))
        self.ed_hi = self.R("lim_hi", QtWidgets.QLineEdit(placeholderText="ex. 4.75k"))
        for w in (self.ed_nom, self.ed_tol, self.ed_lo, self.ed_hi):
            w.setFont(mono(13))
        self.ed_lo.editingFinished.connect(self.update_limits)
        self.ed_hi.editingFinished.connect(self.update_limits)
        for i, (lab, w) in enumerate((("Nominal", self.ed_nom), ("Tolérance %", self.ed_tol), ("Mini", self.ed_lo), ("Maxi", self.ed_hi))):
            box = QtWidgets.QVBoxLayout()
            box.setSpacing(4)
            box.addWidget(QtWidgets.QLabel(lab, objectName="Muted"))
            box.addWidget(w)
            g.addLayout(box, i // 2, i % 2)
        v.addLayout(g)
        row = QtWidgets.QHBoxLayout()
        bt = QtWidgets.QPushButton("Nominal ± % → limites", clicked=self.nom_to_limits)
        row.addWidget(bt)
        row.addWidget(QtWidgets.QPushButton("RAZ compteurs", clicked=self.reset_pf))
        v.addLayout(row)
        self.ck_limbeep = self.R("lim_beep", QtWidgets.QCheckBox("Bip sur FAIL", checked=True))
        v.addWidget(self.ck_limbeep)
        bot = QtWidgets.QHBoxLayout()
        self.lb_limrange = QtWidgets.QLabel("—", objectName="Muted")
        pf = QtWidgets.QLabel("PASS 0 · FAIL 0", objectName="Muted")
        bot.addWidget(self.lb_limrange)
        bot.addStretch()
        bot.addWidget(pf)
        v.addLayout(bot)
        v.addStretch()
        self.st_pf = pf
        return f

    def _w_chart(self):
        f, v = card(None, margins=(14, 12, 14, 12), spacing=8)
        top = QtWidgets.QHBoxLayout()
        top.setSpacing(10)
        self.chart_seg = SegmentBox(["Graphique", "Histogramme"])
        self.chart_seg.set_current(0)
        top.addWidget(self.chart_seg)
        top.addStretch()
        self.ck_chartpause = QtWidgets.QCheckBox("Figer")
        self.ck_mean = QtWidgets.QCheckBox("Moyenne")
        self.ck_region = QtWidgets.QCheckBox("Région d'analyse")
        self.ck_region.toggled.connect(self.toggle_region)
        for w in (self.ck_chartpause, self.ck_mean, self.ck_region):
            top.addWidget(w)
        self.cb_win = self.R("chart_win", QtWidgets.QComboBox(f))
        self.cb_win.addItems(["30 s", "1 min", "5 min", "15 min", "1 h", "Tout"])
        self.cb_win.setCurrentIndex(1)
        self.cb_win.hide()
        self.win_seg = SegmentBox(["30 s", "1 min", "5 min", "15 min", "1 h", "Tout"])
        self.win_seg.set_current(1)
        self.win_seg.picked.connect(self.cb_win.setCurrentIndex)
        top.addWidget(self.win_seg)
        top.addWidget(QtWidgets.QPushButton("Auto-échelle", clicked=self.autoscale))
        v.addLayout(top)
        self.chart_stack = QtWidgets.QStackedWidget()
        self.chart_seg.picked.connect(self.chart_stack.setCurrentIndex)
        p0 = QtWidgets.QWidget(objectName="Transparent")
        l0 = QtWidgets.QVBoxLayout(p0)
        l0.setContentsMargins(0, 0, 0, 0)
        self.plot = self._plot(axisItems={"bottom": pg.DateAxisItem()})
        self.plot.setDownsampling(auto=True, mode="peak")
        self.plot.setClipToView(True)
        self.curve = self.plot.plot()
        self.ln_lo = pg.InfiniteLine(angle=0)
        self.ln_hi = pg.InfiniteLine(angle=0)
        self.ln_mean = pg.InfiniteLine(angle=0)
        for ln in (self.ln_lo, self.ln_hi, self.ln_mean):
            self.plot.addItem(ln)
            ln.hide()
        self.region = pg.LinearRegionItem()
        self.region.sigRegionChanged.connect(self.update_region)
        self.plot.addItem(self.region)
        self.region.hide()
        l0.addWidget(self.plot)
        self.lb_region = QtWidgets.QLabel("")
        self.lb_region.setFont(mono(11))
        self.lb_region.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lb_region.hide()
        l0.addWidget(self.lb_region)
        p1 = QtWidgets.QWidget(objectName="Transparent")
        l1 = QtWidgets.QVBoxLayout(p1)
        l1.setContentsMargins(0, 0, 0, 0)
        hb = QtWidgets.QHBoxLayout()
        self.sp_bins = self.R("hist_bins", QtWidgets.QSpinBox(minimum=5, maximum=500, value=50))
        hb.addWidget(QtWidgets.QLabel("Classes", objectName="Muted"))
        hb.addWidget(self.sp_bins)
        hb.addStretch()
        l1.addLayout(hb)
        self.hplot = self._plot()
        l1.addWidget(self.hplot)
        self.chart_stack.addWidget(p0)
        self.chart_stack.addWidget(p1)
        v.addWidget(self.chart_stack, 1)
        return f

    def _side_panel(self):
        self.side = QtWidgets.QFrame(objectName="SidePanel")
        self.side.setFixedWidth(300)
        v = QtWidgets.QVBoxLayout(self.side)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)
        v.addWidget(card_title("Panneaux"))
        self.dock_checks = {}
        for key, title in DOCKS:
            ck = QtWidgets.QCheckBox(title, checked=True)
            ck.toggled.connect(lambda on, k=key: self.docks[k].setVisible(on))
            self.dock_checks[key] = ck
            v.addWidget(ck)
        v.addSpacing(8)
        v.addWidget(card_title("Dispositions"))
        self.preset_box = QtWidgets.QWidget(objectName="Transparent")
        self.preset_grid = QtWidgets.QGridLayout(self.preset_box)
        self.preset_grid.setContentsMargins(0, 0, 0, 0)
        self.preset_grid.setSpacing(10)
        v.addWidget(self.preset_box)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QPushButton("Enregistrer sous…", clicked=self.save_layout_as))
        row.addWidget(QtWidgets.QPushButton("Supprimer…", clicked=self.delete_layout))
        v.addLayout(row)
        v.addStretch()
        hint = QtWidgets.QLabel("Astuce : un panneau détaché devient une fenêtre indépendante, utile sur un second écran.", objectName="Muted")
        hint.setWordWrap(True)
        v.addWidget(hint)
        self.side.hide()
        self._refresh_presets()
        return self.side

    # ------------------------------------------------------------ autres sections
    def _page(self):
        p = QtWidgets.QWidget(objectName="Page")
        h = QtWidgets.QHBoxLayout(p)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(14)
        return p, h

    def _col(self, width=340):
        w = QtWidgets.QWidget(objectName="Transparent")
        w.setFixedWidth(width)
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)
        return w, v

    def _page_analyse(self):
        p, h = self._page()
        col, cv = self._col()
        f, v = card("Comparaison d'enregistrements")
        v.addWidget(QtWidgets.QPushButton("Charger des CSV…", clicked=self.load_traces))
        v.addWidget(QtWidgets.QPushButton("Ajouter la session courante", clicked=self.add_current_trace))
        self.lw_traces = QtWidgets.QListWidget()
        self.lw_traces.itemChanged.connect(lambda _: self.draw_compare())
        v.addWidget(self.lw_traces, 1)
        v.addWidget(QtWidgets.QPushButton("Supprimer la sélection", clicked=self.remove_trace))
        cv.addWidget(f, 1)
        f, v = card("Intégration (charge / énergie)")
        self.lb_integ = QtWidgets.QLabel("—")
        self.lb_integ.setFont(mono(13))
        self.lb_integ.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.lb_integ)
        fl = QtWidgets.QFormLayout()
        self.ed_volt = self.R("integ_v", QtWidgets.QLineEdit("12"))
        fl.addRow("Tension pour Wh (V)", self.ed_volt)
        v.addLayout(fl)
        v.addWidget(QtWidgets.QPushButton("RAZ intégrale", clicked=self.reset_integ))
        cv.addWidget(f)
        h.addWidget(col)
        f, v = card("Courbes superposées")
        self.cmpplot = self._plot()
        self.cmpplot.setLabel("bottom", "Temps", units="s")
        self.cmpplot.addLegend()
        v.addWidget(self.cmpplot, 1)
        h.addWidget(f, 1)
        return p

    def _page_tri(self):
        p, h = self._page()
        col, cv = self._col()
        f, v = card("Capture automatique")
        fl = QtWidgets.QFormLayout()
        fl.setVerticalSpacing(10)
        self.ck_ah = self.R("ah_en", Switch("Activer"))
        self.sp_ahn = self.R("ah_n", QtWidgets.QSpinBox(minimum=2, maximum=100, value=5))
        self.sp_ahn.valueChanged.connect(lambda n: setattr(self, "ah_buf", deque(maxlen=n)))
        self.sp_ahtol = self.R("ah_tol", QtWidgets.QDoubleSpinBox(decimals=3, minimum=0.001, maximum=50, value=0.2, suffix=" %"))
        self.ed_ahmin = self.R("ah_min", QtWidgets.QLineEdit("0"))
        fl.addRow(self.ck_ah)
        fl.addRow("Échantillons stables", self.sp_ahn)
        fl.addRow("Tolérance", self.sp_ahtol)
        fl.addRow("|Valeur| mini", self.ed_ahmin)
        v.addLayout(fl)
        cv.addWidget(f)
        f, v = card("Tri en bacs")
        fl = QtWidgets.QFormLayout()
        fl.setVerticalSpacing(10)
        self.ck_bin = self.R("bin_en", Switch("Activer"))
        self.cb_bin = self.R("bin_series", QtWidgets.QComboBox())
        self.cb_bin.addItems(list(SERIES))
        self.cb_bin.setCurrentText("E24")
        self.sp_bintol = self.R("bin_tol", QtWidgets.QDoubleSpinBox(decimals=2, minimum=0.01, maximum=50, value=5, suffix=" %"))
        fl.addRow(self.ck_bin)
        fl.addRow("Série", self.cb_bin)
        fl.addRow("Tolérance", self.sp_bintol)
        v.addLayout(fl)
        cv.addWidget(f)
        cv.addStretch()
        h.addWidget(col)
        f, v = card("Captures")
        self.tbl = QtWidgets.QTableWidget(0, 7)
        self.tbl.setHorizontalHeaderLabels(["#", "Heure", "Valeur", "Brut", "Statut", "Bac", "Écart %"])
        self.tbl.horizontalHeader().setStretchLastSection(True)
        self.tbl.verticalHeader().hide()
        self.tbl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        v.addWidget(self.tbl, 1)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QPushButton("Exporter CSV", clicked=self.export_captures))
        row.addWidget(QtWidgets.QPushButton("Vider", clicked=self.clear_captures))
        row.addStretch()
        v.addLayout(row)
        h.addWidget(f, 1)
        f, v = card("Bacs")
        f.setFixedWidth(260)
        self.tbl_bins = QtWidgets.QTableWidget(0, 2)
        self.tbl_bins.setHorizontalHeaderLabels(["Bac", "Nombre"])
        self.tbl_bins.horizontalHeader().setStretchLastSection(True)
        self.tbl_bins.verticalHeader().hide()
        self.tbl_bins.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        v.addWidget(self.tbl_bins, 1)
        h.addWidget(f)
        return p

    def _page_batt(self):
        p, h = self._page()
        col, cv = self._col()
        f, v = card("Décharge batterie")
        fl = QtWidgets.QFormLayout()
        fl.setVerticalSpacing(10)
        self.cb_batload = self.R("bat_load", QtWidgets.QComboBox())
        self.cb_batload.addItems(["Courant constant (A)", "Charge résistive (Ω)"])
        self.ed_batval = self.R("bat_val", QtWidgets.QLineEdit("1"))
        self.ed_batcut = self.R("bat_cut", QtWidgets.QLineEdit("10.5"))
        fl.addRow("Charge", self.cb_batload)
        fl.addRow("Valeur", self.ed_batval)
        fl.addRow("Coupure (V)", self.ed_batcut)
        v.addLayout(fl)
        self.bt_bat = QtWidgets.QPushButton("Démarrer la décharge", checkable=True, objectName="Primary")
        self.bt_bat.toggled.connect(self.toggle_battery)
        v.addWidget(self.bt_bat)
        v.addWidget(QtWidgets.QPushButton("Mémoriser la tension à vide", clicked=self.bat_v0))
        v.addWidget(QtWidgets.QPushButton("Calculer R interne (sous charge)", clicked=self.bat_rint))
        self.lb_bat = QtWidgets.QLabel("Mesure en V DC aux bornes de la batterie.", wordWrap=True)
        self.lb_bat.setFont(mono(12))
        self.lb_bat.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.lb_bat)
        v.addStretch()
        cv.addWidget(f, 1)
        h.addWidget(col)
        f, v = card("Courbe de décharge")
        self.bplot = self._plot()
        self.bplot.setLabel("bottom", "Capacité", units="Ah")
        self.bplot.setLabel("left", "Tension", units="V")
        self.bcurve = self.bplot.plot()
        v.addWidget(self.bplot, 1)
        h.addWidget(f, 1)
        return p

    def _page_conso(self):
        p, h = self._page()
        col, cv = self._col(360)
        f, v = card("Consommation veille / réveil")
        fl = QtWidgets.QFormLayout()
        fl.setVerticalSpacing(10)
        self.ed_cthr = self.R("cons_thr", QtWidgets.QLineEdit("1m"))
        self.ed_ccap = self.R("cons_cap", QtWidgets.QLineEdit("2000"))
        self.ck_clive = self.R("cons_live", Switch("Analyse continue"))
        hb = QtWidgets.QHBoxLayout()
        hb.addWidget(self.ed_cthr)
        hb.addWidget(QtWidgets.QPushButton("Auto", clicked=self.cons_auto))
        fl.addRow("Seuil (A)", hb)
        fl.addRow("Batterie (mAh)", self.ed_ccap)
        fl.addRow(self.ck_clive)
        v.addLayout(fl)
        v.addWidget(QtWidgets.QPushButton("Analyser la fenêtre affichée", clicked=self.cons_analyse, objectName="Primary"))
        self.lb_cons = QtWidgets.QLabel("Mesure en A DC, sur la fenêtre du graphique de la page Mesure.", wordWrap=True)
        self.lb_cons.setFont(mono(12))
        self.lb_cons.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.lb_cons)
        v.addStretch()
        cv.addWidget(f, 1)
        h.addWidget(col)
        f, v = card("Courant")
        self.cplot_c = self._plot(axisItems={"bottom": pg.DateAxisItem()})
        self.ccurve = self.cplot_c.plot()
        self.cthr_line = pg.InfiniteLine(angle=0)
        self.cplot_c.addItem(self.cthr_line)
        v.addWidget(self.cplot_c, 1)
        h.addWidget(f, 1)
        return p

    def _page_seq(self):
        p, h = self._page()
        f, v = card(None)
        hb = QtWidgets.QHBoxLayout()
        hb.addWidget(card_title("Séquence de test"))
        hb.addSpacing(10)
        self.bt_seqrun = QtWidgets.QPushButton("Exécuter", clicked=self.seq_run, objectName="Primary")
        self.bt_seqstop = QtWidgets.QPushButton("Arrêter", clicked=lambda: self.seq.stop())
        for x in (self.bt_seqrun, self.bt_seqstop, QtWidgets.QPushButton("Ouvrir…", clicked=self.seq_load),
                  QtWidgets.QPushButton("Enregistrer…", clicked=self.seq_save),
                  QtWidgets.QPushButton("Exemple", clicked=lambda: self.ed_seq.setPlainText(EXAMPLE)),
                  QtWidgets.QPushButton("Aide", clicked=lambda: QtWidgets.QMessageBox.information(self, "Séquences", HELP))):
            hb.addWidget(x)
        hb.addStretch()
        self.lb_verdict = QtWidgets.QLabel("")
        self.lb_verdict.setFont(ui_font(18, QtGui.QFont.Bold))
        hb.addWidget(self.lb_verdict)
        v.addLayout(hb)
        sp = QtWidgets.QSplitter()
        self.ed_seq = QtWidgets.QPlainTextEdit()
        self.ed_seq.setFont(mono(13))
        self.ed_seq.setPlainText(self.cfg.value("seq_text", EXAMPLE))
        self.seq_out = QtWidgets.QPlainTextEdit(readOnly=True)
        self.seq_out.setFont(mono(13))
        sp.addWidget(self.ed_seq)
        sp.addWidget(self.seq_out)
        v.addWidget(sp, 1)
        h.addWidget(f, 1)
        return p

    def _page_journal(self):
        p, h = self._page()
        sp = QtWidgets.QSplitter()
        f, v = card("Événements")
        self.tbl_ev = QtWidgets.QTableWidget(0, 4)
        self.tbl_ev.setHorizontalHeaderLabels(["Heure", "Type", "Valeur", "Détail"])
        self.tbl_ev.horizontalHeader().setStretchLastSection(True)
        self.tbl_ev.verticalHeader().hide()
        self.tbl_ev.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        v.addWidget(self.tbl_ev, 1)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QPushButton("Exporter CSV", clicked=lambda: self._export_table(self.tbl_ev, "evenements.csv")))
        row.addWidget(QtWidgets.QPushButton("Vider", clicked=self.clear_events))
        row.addStretch()
        v.addLayout(row)
        sp.addWidget(f)
        f, v = card("Console SCPI")
        self.con_out = QtWidgets.QPlainTextEdit(readOnly=True)
        self.con_out.setFont(mono(12))
        self.con_in = QtWidgets.QLineEdit(placeholderText="Commande SCPI (ex. FUNC?, RANGE?, CALC:AVER:ALL?) + Entrée")
        self.con_in.setFont(mono(13))
        self.con_in.returnPressed.connect(self.console_send)
        self.con_hist, self.con_hi = [], 0
        self.con_in.installEventFilter(self)
        v.addWidget(self.con_out, 1)
        v.addWidget(self.con_in)
        sp.addWidget(f)
        h.addWidget(sp, 1)
        return p

    def _group(self, grid, title, pos):
        f, v = card(title)
        fl = QtWidgets.QFormLayout()
        fl.setVerticalSpacing(10)
        fl.setHorizontalSpacing(14)
        v.addLayout(fl)
        v.addStretch()
        grid.addWidget(f, *pos)
        return fl

    def _page_reglages(self):
        sa = QtWidgets.QScrollArea(widgetResizable=True)
        inner = QtWidgets.QWidget(objectName="Transparent")
        g = QtWidgets.QGridLayout(inner)
        g.setContentsMargins(0, 0, 8, 0)
        g.setSpacing(14)
        for c in range(3):
            g.setColumnStretch(c, 1)
        sa.setWidget(inner)

        fl = self._group(g, "Acquisition & affichage", (0, 0))
        self.sp_int = self.R("interval", QtWidgets.QSpinBox(suffix=" ms", maximum=3_600_000, singleStep=100))
        self.sp_int.valueChanged.connect(lambda v: self.worker and setattr(self.worker, "interval", v / 1000))
        self.sp_dig = self.R("digits", QtWidgets.QSpinBox(minimum=3, maximum=8, value=6))
        self.sp_avg = self.R("avg", QtWidgets.QSpinBox(minimum=1, maximum=500, value=1))
        self.sp_avg.valueChanged.connect(lambda n: (setattr(self, "filt", deque(self.filt, maxlen=n)), self.update_mode_label()))
        fl.addRow("Intervalle entre mesures", self.sp_int)
        fl.addRow("Chiffres significatifs", self.sp_dig)
        fl.addRow("Moyenne glissante (N)", self.sp_avg)

        fl = self._group(g, "Maths de l'appareil", (0, 1))
        self.cb_math = QtWidgets.QComboBox()
        self.cb_math.addItems(["Aucune", "NULL (relatif)", "dB", "dBm", "Statistiques"])
        self.cb_dbref = QtWidgets.QComboBox()
        self.cb_dbref.addItems(DB_REFS)
        self.cb_dbref.setCurrentText("600")
        self.ed_null = QtWidgets.QLineEdit(placeholderText="offset ex. 12m")
        hb = QtWidgets.QHBoxLayout()
        hb.addWidget(QtWidgets.QPushButton("Appliquer", clicked=self.apply_math, objectName="Primary"))
        hb.addWidget(QtWidgets.QPushButton("Lire stats DMM", clicked=lambda: self.cmd("CALC:AVER:ALL?", "calc_all")))
        fl.addRow("Fonction", self.cb_math)
        fl.addRow("Réf. dB/dBm (Ω)", self.cb_dbref)
        fl.addRow("Offset NULL", self.ed_null)
        fl.addRow(hb)

        fl = self._group(g, "Réglages appareil", (0, 2))
        self.cb_tunit = self.R("tunit", QtWidgets.QComboBox())
        self.cb_tunit.addItems(["C", "F", "K"])
        self.cb_tunit.activated.connect(self.set_tunit)
        self.ed_cont = self.R("cont", QtWidgets.QLineEdit("50"))
        self.ed_cont.editingFinished.connect(lambda: self.cmd(f"CONT:THRE {self.ed_cont.text().strip()}"))
        self.ck_beep = Switch("Buzzer du multimètre")
        self.ck_beep.setChecked(True)
        self.ck_beep.toggled.connect(lambda on: self.cmd(f"SYST:BEEP:STAT {'ON' if on else 'OFF'}"))
        fl.addRow("Unité température", self.cb_tunit)
        fl.addRow("Seuil continuité (Ω)", self.ed_cont)
        fl.addRow(self.ck_beep)
        fl.addRow(QtWidgets.QPushButton("Reset appareil (*RST)", clicked=lambda: self.cmd("*RST")))

        fl = self._group(g, "Enregistrement CSV", (1, 0))
        self.cb_trig = self.R("trig", QtWidgets.QComboBox())
        self.cb_trig.addItems(["Immédiat", "Seuil montant", "Seuil descendant", "À une heure"])
        self.ed_trigthr = self.R("trig_thr", QtWidgets.QLineEdit("0"))
        self.te_trig = self.R("trig_time", QtWidgets.QTimeEdit(QtCore.QTime.currentTime().addSecs(600)))
        self.te_trig.setDisplayFormat("HH:mm:ss")
        self.sp_recdur = self.R("rec_dur", QtWidgets.QSpinBox(maximum=10_000_000, suffix=" s", specialValueText="illimitée"))
        self.sp_recdec = self.R("rec_dec", QtWidgets.QSpinBox(minimum=1, maximum=10000, value=1, prefix="1 mesure / "))
        self.ck_csvfr = QtWidgets.QCheckBox("Format Excel FR ( ; et virgule)", checked=True)
        self.bt_rec = QtWidgets.QPushButton("Démarrer / armer", checkable=True, objectName="Primary")
        self.bt_rec.toggled.connect(self.toggle_rec)
        self.lb_rec = QtWidgets.QLabel("—", wordWrap=True, objectName="Muted")
        fl.addRow("Déclencheur", self.cb_trig)
        fl.addRow("Seuil", self.ed_trigthr)
        fl.addRow("Heure", self.te_trig)
        fl.addRow("Durée", self.sp_recdur)
        fl.addRow("Décimation", self.sp_recdec)
        fl.addRow(self.ck_csvfr)
        fl.addRow(self.bt_rec)
        fl.addRow(self.lb_rec)

        fl = self._group(g, "Surveillance (anomalies)", (1, 1))
        self.ck_an = self.R("an_en", Switch("Activer"))
        self.ck_an_ol = self.R("an_ol", QtWidgets.QCheckBox("Dépassement / OL", checked=True))
        self.ck_an_spike = self.R("an_spike", QtWidgets.QCheckBox("Pics", checked=True))
        self.sp_an_k = self.R("an_k", QtWidgets.QDoubleSpinBox(minimum=2, maximum=100, value=6, suffix=" σ"))
        self.sp_an_n = self.R("an_n", QtWidgets.QSpinBox(minimum=5, maximum=1000, value=30, suffix=" pts"))
        self.ed_an_low = self.R("an_low", QtWidgets.QLineEdit(placeholderText="vide = désactivé"))
        self.ck_an_drift = self.R("an_drift", QtWidgets.QCheckBox("Dérive"))
        self.sp_an_dthr = self.R("an_dthr", QtWidgets.QDoubleSpinBox(decimals=3, minimum=0.001, maximum=100, value=0.5, suffix=" %/min"))
        self.sp_an_dwin = self.R("an_dwin", QtWidgets.QSpinBox(minimum=10, maximum=3600, value=60, suffix=" s"))
        self.ck_an_beep = self.R("an_beep", QtWidgets.QCheckBox("Bip / annonce", checked=True))
        fl.addRow(self.ck_an)
        fl.addRow(self.ck_an_ol)
        hb = QtWidgets.QHBoxLayout()
        for w in (self.ck_an_spike, self.sp_an_k, self.sp_an_n):
            hb.addWidget(w)
        fl.addRow(hb)
        fl.addRow("Décrochage si |v| <", self.ed_an_low)
        hb = QtWidgets.QHBoxLayout()
        for w in (self.ck_an_drift, self.sp_an_dthr, self.sp_an_dwin):
            hb.addWidget(w)
        fl.addRow(hb)
        fl.addRow(self.ck_an_beep)

        fl = self._group(g, "Annonce vocale", (1, 2))
        self.cb_tts = self.R("tts", QtWidgets.QComboBox())
        self.cb_tts.addItems(["Désactivée", "Périodique", "Sur capture", "Sur FAIL / anomalie", "Capture + FAIL"])
        self.sp_tts = self.R("tts_int", QtWidgets.QSpinBox(minimum=2, maximum=3600, value=10, suffix=" s"))
        fl.addRow("Mode", self.cb_tts)
        fl.addRow("Période", self.sp_tts)
        fl.addRow(QtWidgets.QPushButton("Tester", clicked=lambda: self.speaker.say(
            speak_si(self.last_v, self.unit(), 4, self.noprefix()) if self.last_v is not None else "Synthèse vocale active")))

        fl = self._group(g, "Services réseau", (2, 0))
        self.ck_web = Switch("Serveur web local")
        self.sp_webport = QtWidgets.QSpinBox(minimum=1024, maximum=65535, value=8088)
        self.ck_web.toggled.connect(self.toggle_web)
        self.lb_web = QtWidgets.QLabel("", openExternalLinks=True, textInteractionFlags=Qt.TextBrowserInteraction)
        self.ck_mqtt = Switch("Publication MQTT")
        self.ck_mqtt.toggled.connect(self.toggle_mqtt)
        self.lb_mqtt = QtWidgets.QLabel("", wordWrap=True, objectName="Muted")
        fl.addRow(self.ck_web)
        fl.addRow("Port", self.sp_webport)
        fl.addRow(self.lb_web)
        fl.addRow(self.ck_mqtt)
        fl.addRow(QtWidgets.QPushButton("Configurer MQTT…", clicked=self.mqtt_dialog))
        fl.addRow(self.lb_mqtt)
        g.setRowStretch(3, 1)
        return sa

    def _shortcuts(self):
        for key, fn in (("H", lambda: self.bt_hold.toggle()), ("R", lambda: self.bt_rel.toggle()),
                        ("Space", lambda: self.capture(self.last_v)), ("P", lambda: self.bt_run.toggle()),
                        ("C", self.copy_value), ("A", self.autoscale)):
            sc = QtGui.QShortcut(QtGui.QKeySequence(key), self)
            sc.activated.connect(fn)

    # ================================================================== navigation / thème / disposition
    def goto(self, key):
        self.section = key
        self.stack.setCurrentIndex(self.pages[key])
        self.nav_rail.select(key)
        self.nav_tabs.select(key)
        if key == "batt" or key == "conso":
            self.refresh_views()

    def set_option(self, k, v):
        t = dict(self.theme_t)
        t[k] = v
        self.apply_theme(t, save=True)

    def open_appearance(self):
        AppearanceDialog(self, self.theme_t).exec()

    def apply_theme(self, t, save=False):
        self.theme_t = TH.complete(t)
        t = self.theme_t
        app = QtWidgets.QApplication.instance()
        TH.apply(app, t)
        skin, nav = t["skin"], t["nav"]
        self.nav_rail.setVisible(nav == "rail")
        self.tabsrow.setVisible(nav == "tabs")
        self.disp.apply_theme(skin)
        self.fpad.set_skin(skin)
        self.range_sel.set_skin(skin)
        self.func_card.setProperty("skin", skin)
        self.func_card.style().unpolish(self.func_card)
        self.func_card.style().polish(self.func_card)
        for c in self.statsp.cards.values():
            c.refont()
        self.logo.setPixmap(TH.svg_pixmap(TH.ICONS["logo"], t["accent"], 26, stroke=2.2))
        self.banner_icon.setPixmap(TH.svg_pixmap(TH.ICONS["layout"], t["sec"], 20))
        self.bt_refresh.setIcon(TH.icon("refresh", t["text"], 16))
        self.bt_theme.setIcon(TH.icon("palette", t["text"], 18))
        self.bt_menu.setIcon(TH.icon("menu", t["text"], 18))
        self.bt_layout.setIcon(TH.icon("layout", t["accent"] if self.edit_mode else t["text"], 16))
        for a in self.skin_group.actions():
            a.setChecked(a.data() == skin)
        for a in self.nav_group.actions():
            a.setChecked(a.data() == nav)
        self._theme_plots()
        if self.floating:
            self.floating.refresh_theme()
        self.disp_color = None
        self.update_mode_label()
        self.set_zoom(int(self.cfg.value("zoom", 100)), save=False)
        if save:
            self.cfg.setValue("theme_json", TH.to_json(t))

    def _theme_plots(self):
        t = self.theme_t
        axis_pen = pg.mkPen(mix(t["grid"], t["muted"], 0.45))
        for p in self.plots:
            p.setBackground(t["plot"])
            for ax in ("left", "bottom"):
                p.getAxis(ax).setPen(axis_pen)
                p.getAxis(ax).setTextPen(pg.mkPen(t["muted"]))
        self.curve.setPen(pg.mkPen(t["curve"], width=t["curve_width"]))
        for ln in (self.ln_lo, self.ln_hi):
            ln.setPen(pg.mkPen(t["fail"], width=1.2, style=Qt.DashLine))
        self.ln_mean.setPen(pg.mkPen(t["warn"], width=1.2, style=Qt.DotLine))
        sc = QtGui.QColor(t["sec"])
        sc.setAlpha(45)
        self.region.setBrush(pg.mkBrush(sc))
        self.bcurve.setPen(pg.mkPen(t["curve"], width=2))
        self.ccurve.setPen(pg.mkPen(t["sec"], width=1.4))
        self.cthr_line.setPen(pg.mkPen(t["warn"], style=Qt.DashLine))

    def set_zoom(self, pct, save=True):
        app = QtWidgets.QApplication.instance()
        f = QtGui.QFont(TH.UI_FONT)
        f.setPointSizeF(9.5 * pct / 100)
        app.setFont(f)
        for a in self.zoom_group.actions():
            a.setChecked(a.data() == pct)
        if save:
            self.cfg.setValue("zoom", pct)

    # ------------------------------------------------------------ disposition
    def _dock_title(self, key):
        d = self.docks[key]
        if self.edit_mode or d.isFloating():
            d.setTitleBarWidget(self.dock_titles[key])
            self.dock_titles[key].show()
            d.setFeatures(QtWidgets.QDockWidget.DockWidgetMovable | QtWidgets.QDockWidget.DockWidgetFloatable)
        else:
            self.dock_titles[key].hide()
            d.setTitleBarWidget(QtWidgets.QWidget())
            d.setFeatures(QtWidgets.QDockWidget.NoDockWidgetFeatures)

    def _sync_dock_check(self, key):
        ck = self.dock_checks.get(key)
        if ck:
            ck.blockSignals(True)
            ck.setChecked(not self.docks[key].isHidden())
            ck.blockSignals(False)

    def set_edit_mode(self, on):
        if on == self.edit_mode:
            return
        if on:
            self._edit_snapshot = self.dockhost.saveState(LAYOUT_VERSION)
            self.goto("mesure")
        self.edit_mode = on
        for key, d in self.docks.items():
            self._dock_title(key)
            w = d.widget()
            w.setProperty("edit", on)
            w.style().unpolish(w)
            w.style().polish(w)
            self._sync_dock_check(key)
        self.banner.setVisible(on)
        self.side.setVisible(on)
        self.bt_layout.blockSignals(True)
        self.bt_layout.setChecked(on)
        self.bt_layout.blockSignals(False)
        self.bt_layout.setIcon(TH.icon("layout", self.theme_t["accent"] if on else self.theme_t["text"], 16))
        (self.title_timer.start if on else self.title_timer.stop)()

    def save_edit(self):
        self.cfg.setValue("layout_state", bytes(self.dockhost.saveState(LAYOUT_VERSION).toBase64()).decode())
        self.set_edit_mode(False)
        self.statusBar().showMessage("Disposition enregistrée", 3000)

    def cancel_edit(self):
        if getattr(self, "_edit_snapshot", None) is not None:
            self.dockhost.restoreState(self._edit_snapshot, LAYOUT_VERSION)
        self.set_edit_mode(False)

    def custom_layouts(self):
        try:
            return json.loads(self.cfg.value("layout_custom", "{}"))
        except (ValueError, TypeError):
            return {}

    def _refresh_presets(self):
        while self.preset_grid.count():
            it = self.preset_grid.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.menu_layouts.clear()
        names = list(LAYOUTS) + list(self.custom_layouts())
        for i, name in enumerate(names):
            b = PresetButton(name, LAYOUTS.get(name, [(0, 0, 1, 1, 0)]))
            b.setCheckable(False)
            b.clicked.connect(lambda _=False, n=name: self.apply_layout(n))
            self.preset_grid.addWidget(b, i // 2, i % 2)
            self.menu_layouts.addAction(name, lambda n=name: self.apply_layout(n))
        self.menu_layouts.addSeparator()
        self.menu_layouts.addAction("Modifier la disposition…", lambda: self.set_edit_mode(True))

    def save_layout_as(self):
        name, ok = QtWidgets.QInputDialog.getText(self, "Disposition", "Nom de la disposition :")
        if ok and name.strip() and name.strip() not in LAYOUTS:
            c = self.custom_layouts()
            c[name.strip()] = bytes(self.dockhost.saveState(LAYOUT_VERSION).toBase64()).decode()
            self.cfg.setValue("layout_custom", json.dumps(c))
            self._refresh_presets()

    def delete_layout(self):
        c = self.custom_layouts()
        if not c:
            return
        name, ok = QtWidgets.QInputDialog.getItem(self, "Disposition", "Supprimer :", sorted(c), 0, False)
        if ok:
            c.pop(name, None)
            self.cfg.setValue("layout_custom", json.dumps(c))
            self._refresh_presets()

    def apply_layout(self, name):
        host, D = self.dockhost, self.docks
        custom = self.custom_layouts()
        if name in custom:
            host.restoreState(QtCore.QByteArray.fromBase64(custom[name].encode()), LAYOUT_VERSION)
            return
        for d in D.values():
            d.setFloating(False)
            host.removeDockWidget(d)
        H, V = Qt.Horizontal, Qt.Vertical
        L, T, B = Qt.LeftDockWidgetArea, Qt.TopDockWidgetArea, Qt.BottomDockWidgetArea
        if name == "Grand afficheur":
            show = ["display", "actions", "stats", "chart"]
            host.addDockWidget(T, D["display"])
            host.splitDockWidget(D["display"], D["actions"], V)
            host.splitDockWidget(D["actions"], D["stats"], V)
            host.splitDockWidget(D["stats"], D["chart"], V)
            sizes = [(["display", "actions", "stats", "chart"], [460, 48, 76, 280], V)]
        elif name == "Analyse":
            show = ["display", "actions", "stats", "chart"]
            host.addDockWidget(T, D["display"])
            host.splitDockWidget(D["display"], D["stats"], H)
            host.splitDockWidget(D["display"], D["actions"], V)
            host.addDockWidget(B, D["chart"])
            sizes = [(["display", "stats"], [760, 600], H), (["display", "chart"], [210, 560], V)]
        elif name == "Compact":
            show = ["display", "actions"]
            host.addDockWidget(T, D["display"])
            host.splitDockWidget(D["display"], D["actions"], V)
            sizes = [(["display", "actions"], [640, 48], V)]
        else:
            show = [k for k, _ in DOCKS]
            host.addDockWidget(L, D["function"])
            host.splitDockWidget(D["function"], D["display"], H)
            host.splitDockWidget(D["function"], D["limits"], V)
            host.splitDockWidget(D["display"], D["actions"], V)
            host.splitDockWidget(D["actions"], D["stats"], V)
            host.splitDockWidget(D["stats"], D["chart"], V)
            sizes = [(["function", "display"], [360, 1080], H), (["display", "actions", "stats", "chart"], [250, 48, 76, 430], V),
                     (["function", "limits"], [560, 250], V)]
        for k, d in D.items():
            d.setVisible(k in show)
            self._sync_dock_check(k)

        def resize():
            for keys, px, orient in sizes:
                host.resizeDocks([D[k] for k in keys], px, orient)
        QtCore.QTimer.singleShot(0, resize)

    def toggle_compact(self):
        if self.act_compact.isChecked():
            self.compact_state = self.dockhost.saveState(LAYOUT_VERSION)
            self.apply_layout("Compact")
            self.nav_rail.hide()
            self.tabsrow.hide()
            self.goto("mesure")
        else:
            if self.compact_state is not None:
                self.dockhost.restoreState(self.compact_state, LAYOUT_VERSION)
            self.nav_rail.setVisible(self.theme_t["nav"] == "rail")
            self.tabsrow.setVisible(self.theme_t["nav"] == "tabs")

    # ================================================================== réglages / profils
    def _load_settings(self):
        c = self.cfg
        b = lambda k, d: str(c.value(k, d)).lower() == "true"
        self.act_autorec.setChecked(b("autorec", True))
        self.act_fast.setChecked(b("fast", True))
        self.act_restore.setChecked(b("restore", True))
        self.act_rangecmd.setChecked(b("rangecmd", True))
        self.act_autoupd.setChecked(b("autoupd", True))
        self.ck_csvfr.setChecked(b("csvfr", True))
        self.ck_beep.blockSignals(True)
        self.ck_beep.setChecked(b("beep", True))
        self.ck_beep.blockSignals(False)
        self.sp_webport.setValue(int(c.value("web_port", 8088)))
        g = c.value("geom")
        if g:
            self.restoreGeometry(g)
        if b("web", False):
            self.ck_web.setChecked(True)
        if b("mqtt", False):
            self.ck_mqtt.setChecked(True)

    def _save_settings(self):
        c = self.cfg
        c.setValue("port", self.cb_port.currentData() or c.value("port", ""))
        for k, a in (("autorec", self.act_autorec), ("fast", self.act_fast), ("restore", self.act_restore), ("rangecmd", self.act_rangecmd), ("autoupd", self.act_autoupd)):
            c.setValue(k, "true" if a.isChecked() else "false")
        for k, w in (("csvfr", self.ck_csvfr), ("beep", self.ck_beep), ("web", self.ck_web), ("mqtt", self.ck_mqtt)):
            c.setValue(k, "true" if w.isChecked() else "false")
        c.setValue("web_port", self.sp_webport.value())
        c.setValue("geom", self.saveGeometry())
        c.setValue("seq_text", self.ed_seq.toPlainText())
        c.setValue("last_state", json.dumps(self.get_state()))
        if not self.edit_mode and not self.act_compact.isChecked():
            c.setValue("layout_state", bytes(self.dockhost.saveState(LAYOUT_VERSION).toBase64()).decode())

    def get_state(self):
        st = {"mode": self.mode}
        for k, w in self.persist.items():
            if isinstance(w, QtWidgets.QComboBox):
                st[k] = w.currentIndex()
            elif isinstance(w, QtWidgets.QCheckBox):
                st[k] = w.isChecked()
            elif isinstance(w, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
                st[k] = w.value()
            elif isinstance(w, QtWidgets.QTimeEdit):
                st[k] = w.time().toString("HH:mm:ss")
            elif isinstance(w, QtWidgets.QLineEdit):
                st[k] = w.text()
        return st

    def set_state(self, st, device=True):
        mode = st.get("mode", self.mode)
        if mode in MODES:
            self.set_mode(mode, send=device and bool(self.worker))
        for k, val in st.items():
            w = self.persist.get(k)
            if w is None:
                continue
            w.blockSignals(True)
            try:
                if isinstance(w, QtWidgets.QComboBox) and 0 <= int(val) < w.count():
                    w.setCurrentIndex(int(val))
                elif isinstance(w, QtWidgets.QCheckBox):
                    w.setChecked(bool(val))
                elif isinstance(w, QtWidgets.QSpinBox):
                    w.setValue(int(val))
                elif isinstance(w, QtWidgets.QDoubleSpinBox):
                    w.setValue(float(val))
                elif isinstance(w, QtWidgets.QTimeEdit):
                    w.setTime(QtCore.QTime.fromString(str(val), "HH:mm:ss"))
                elif isinstance(w, QtWidgets.QLineEdit):
                    w.setText(str(val))
            except (ValueError, TypeError):
                pass
            w.blockSignals(False)
            w.update()
        self.filt = deque(maxlen=self.sp_avg.value())
        self.ah_buf = deque(maxlen=self.sp_ahn.value())
        if self.worker:
            self.worker.interval = self.sp_int.value() / 1000
        self.win_seg.set_current(self.cb_win.currentIndex())
        self.update_limits()
        self.update_mode_label()
        if device and self.worker:
            if self.cb_range.currentIndex() > 0 or mode == "TEMP":
                self.set_range(self.cb_range.currentIndex())
            self.set_speed(self.cb_speed.currentIndex())
            self.set_dual(self.ck_dual.isChecked() and bool(MODES[self.mode].get("dual")))

    def _profiles_path(self):
        return os.path.join(data_dir(), "profiles.json")

    def _load_profiles(self):
        try:
            with open(self._profiles_path(), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return dict(DEFAULT_PROFILES)

    def _save_profiles(self):
        with open(self._profiles_path(), "w", encoding="utf-8") as f:
            json.dump(self.profiles, f, ensure_ascii=False, indent=1)

    def _refresh_profile_menu(self):
        self.menu_prof.clear()
        for name in sorted(self.profiles):
            self.menu_prof.addAction(name, lambda n=name: self.load_profile(n))
        self.menu_prof.addSeparator()
        self.menu_prof.addAction("Enregistrer le réglage actuel…", self.save_profile)
        self.menu_prof.addAction("Supprimer un profil…", self.delete_profile)
        self.menu_prof.addAction("Restaurer les profils d'exemple", self.restore_profiles)
        self.cb_profile.blockSignals(True)
        self.cb_profile.clear()
        self.cb_profile.addItem("— choisir —")
        self.cb_profile.addItems(sorted(self.profiles))
        self.cb_profile.blockSignals(False)

    def load_profile(self, name):
        st = self.profiles.get(name)
        if st is None:
            return
        self.set_state(st, device=True)
        self.statusBar().showMessage(f"Profil chargé : {name}", 4000)

    def save_profile(self):
        name, ok = QtWidgets.QInputDialog.getText(self, "Profil", "Nom du profil :")
        if ok and name.strip():
            self.profiles[name.strip()] = self.get_state()
            self._save_profiles()
            self._refresh_profile_menu()

    def delete_profile(self):
        if not self.profiles:
            return
        name, ok = QtWidgets.QInputDialog.getItem(self, "Profil", "Supprimer :", sorted(self.profiles), 0, False)
        if ok:
            self.profiles.pop(name, None)
            self._save_profiles()
            self._refresh_profile_menu()

    def restore_profiles(self):
        self.profiles.update(DEFAULT_PROFILES)
        self._save_profiles()
        self._refresh_profile_menu()

    # ================================================================== connexion
    def refresh_ports(self):
        self.cb_port.clear()
        saved = self.cfg.value("port", "")
        best = -1
        for i, p in enumerate(sorted(serial.tools.list_ports.comports(), key=lambda p: p.device)):
            self.cb_port.addItem(f"{p.device} — {p.description}", p.device)
            if p.device == saved:
                best = i
        if best >= 0:
            self.cb_port.setCurrentIndex(best)

    def detect_port(self):
        QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
        found = None
        try:
            for i in range(self.cb_port.count()):
                port = self.cb_port.itemData(i)
                try:
                    with serial.Serial(port, 115200, timeout=0.8, write_timeout=0.5) as s:
                        s.reset_input_buffer()
                        s.write(b"SYST:REM\n")
                        time.sleep(0.1)
                        s.reset_input_buffer()
                        s.write(b"*IDN?\n")
                        r = s.readline().decode("ascii", "ignore")
                        s.write(b"SYST:LOC\n")
                        if "XDM" in r.upper() or "OWON" in r.upper():
                            found = i
                            break
                except Exception:
                    continue
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        if found is None:
            QtWidgets.QMessageBox.warning(self, APP, "Aucun XDM1041 détecté.")
        else:
            self.cb_port.setCurrentIndex(found)
            self.statusBar().showMessage(f"XDM1041 trouvé sur {self.cb_port.itemData(found)}", 5000)

    def _pill(self, text, state=None):
        self.lb_idn.setText(text)
        for prop in ("on", "lost"):
            self.lb_idn.setProperty(prop, state == prop)
        self.lb_idn.style().unpolish(self.lb_idn)
        self.lb_idn.style().polish(self.lb_idn)

    def toggle_connect(self):
        if self.worker:
            self.disconnect_dmm()
            return
        port = self.cb_port.currentData()
        if not port:
            return
        self.first_connect = True
        self.worker = DmmWorker(port, self.act_autorec.isChecked())
        self.act_autorec.toggled.connect(self._autorec_changed)
        self.worker.interval = self.sp_int.value() / 1000
        self.worker.polling = not self.bt_run.isChecked()
        self.worker.reading.connect(self.on_reading)
        self.worker.reply.connect(self.on_reply)
        self.worker.error.connect(self.on_error)
        self.worker.lost.connect(self.on_lost)
        self.worker.connected.connect(self.on_connected)
        self.worker.finished.connect(self.on_finished)
        self.worker.start()
        self.bt_conn.setText("Connexion…")
        self.bt_conn.setEnabled(False)

    def _autorec_changed(self, on):
        if self.worker:
            self.worker.auto_reconnect = on

    def disconnect_dmm(self):
        if self.worker:
            self.worker.running = False
            self.worker.wait(4000)

    def on_connected(self, idn):
        self.idn = idn
        parts = idn.split(",")
        self._pill(f"● {self.cb_port.currentText().split(' — ')[0]} · {parts[1] if len(parts) > 1 else 'XDM'} · {parts[3] if len(parts) > 3 else ''}".rstrip(" ·"), "on")
        self.lb_idn.setToolTip(idn)
        self.bt_conn.setText("Déconnecter")
        self.bt_conn.setChecked(False)
        self.bt_conn.setEnabled(True)
        self.log(f"<< {idn}")
        self.web.set(online=True)
        if self.first_connect and not self.act_restore.isChecked():
            if self.act_fast.isChecked():
                self.cmd("RATE F")
            for c, tag in (("FUNC?", "sync_func"), ("RATE?", "sync_rate"), ("FUNC2?", "sync_func2")):
                self.cmd(c, tag)
        else:
            if self.first_connect and self.act_fast.isChecked():
                self.cb_speed.setCurrentIndex(2)
            self.apply_device_config()
        if not self.first_connect:
            self.add_event("Liaison", None, "reconnecté")
        self.first_connect = False
        if self.ck_mqtt.isChecked():
            self.toggle_mqtt(True)
        self._save_settings()

    def apply_device_config(self):
        self.set_mode(self.mode, send=True, clear=False)
        if self.cb_range.currentIndex() > 0:
            self.set_range(self.cb_range.currentIndex())
        self.set_speed(self.cb_speed.currentIndex())
        if self.ck_dual.isChecked() and MODES[self.mode].get("dual"):
            self.set_dual(True)
        self.cmd(f"SYST:BEEP:STAT {'ON' if self.ck_beep.isChecked() else 'OFF'}")

    def on_lost(self, msg):
        self._pill("⚠ Liaison perdue — reconnexion…", "lost")
        self.statusBar().showMessage(msg, 5000)
        self.web.set(online=False)
        self.add_event("Liaison", None, msg)

    def on_finished(self):
        self.worker = None
        try:
            self.act_autorec.toggled.disconnect(self._autorec_changed)
        except (RuntimeError, TypeError):
            pass
        self.bt_conn.setText("Connecter")
        self.bt_conn.setEnabled(True)
        self._pill("Déconnecté")
        self.disp.set_rate("")
        self.web.set(online=False)

    def on_error(self, msg):
        self.statusBar().showMessage(msg, 10000)
        self.log(f"!! {msg}")

    def cmd(self, c, tag=None):
        if not self.worker:
            self.statusBar().showMessage("Non connecté", 3000)
            return
        self.worker.send(c, tag)

    def log(self, s):
        self.con_out.appendPlainText(f"[{datetime.datetime.now():%H:%M:%S}] {s}")

    def on_reply(self, tag, r):
        r0 = r.strip().strip('"').upper()
        if tag == "sync_func" and r0 in FUNC_MAP:
            self.set_mode(FUNC_MAP[r0], send=False)
        elif tag == "sync_rate" and r0[:1] in ("S", "L", "M", "F"):
            self.cb_speed.setCurrentIndex({"S": 0, "L": 0, "M": 1, "F": 2}[r0[:1]])
            self.update_mode_label()
        elif tag == "rate_chk":
            got = r0[:1]
            if self.want_rate == 0 and got not in ("S", "L") and not self.rate_retry:
                self.rate_retry = True
                self.slow_letter = "L" if self.slow_letter == "S" else "S"
                self.cfg.setValue("slow_letter", self.slow_letter)
                self.cmd(f"RATE {self.slow_letter}")
                self.cmd("RATE?", "rate_chk")
                return
            if got in ("S", "L", "M", "F"):
                self.cb_speed.setCurrentIndex({"S": 0, "L": 0, "M": 1, "F": 2}[got])
                if self.want_rate is not None and self.cb_speed.currentIndex() != self.want_rate:
                    self.statusBar().showMessage(f"Le DMM a refusé la vitesse demandée (répond {got})", 6000)
            self.update_mode_label()
        elif tag == "sync_func2":
            dual = r0.startswith("FREQ")
            self.ck_dual.blockSignals(True)
            self.ck_dual.setChecked(dual)
            self.ck_dual.blockSignals(False)
            if self.worker:
                self.worker.dual = dual
        elif tag == "calc_all":
            self.log(f"Stats DMM (min, max, moy, n) : {r}")
            self.goto("journal")
        elif tag == "console":
            self.log(f"<< {r}")
        elif tag == "seq":
            self.seq.on_reply(r)

    # ================================================================== commandes DMM
    def on_func_pick(self, key):
        if key == "AUTO":
            if MODES[self.mode]["ranges"] and self.mode != "TEMP":
                self.on_range_pick(0)
        else:
            self.set_mode(key)

    def on_range_pick(self, i):
        self.cb_range.setCurrentIndex(i)
        self.set_range(i)

    def on_speed_pick(self, i):
        self.cb_speed.setCurrentIndex(i)
        self.set_speed(i)

    def set_mode(self, key, send=True, clear=True):
        m = MODES[key]
        changed = key != self.mode
        self.mode = key
        self.unit_override = None
        self.fpad.set_current(key)
        if changed or self.cb_range.count() != len(m["ranges"]):
            self.cb_range.blockSignals(True)
            self.cb_range.clear()
            for lab, _ in m["ranges"]:
                self.cb_range.addItem(lab)
            self.cb_range.blockSignals(False)
            self.range_sel.set_items([lab for lab, _ in m["ranges"]])
        self.ck_dual.setEnabled(bool(m.get("dual")))
        if not m.get("dual") and self.worker:
            self.worker.dual = False
        if send:
            self.ignore_until = time.time() + 0.6
            if key == "TEMP":
                self.cmd(f"{m['cmd']} {m['ranges'][max(0, self.cb_range.currentIndex())][1]}")
                self.cmd(f"TEMP:RTD:UNIT {self.cb_tunit.currentText()}")
            else:
                self.cmd(m["cmd"] + (" AUTO" if m["ranges"] else ""))
                self.cb_range.setCurrentIndex(0)
            if m.get("dual") and self.ck_dual.isChecked():
                self.set_dual(True)
        self.update_mode_label()
        if clear and (changed or send):
            self.clear_data()

    def select_range(self, label):
        m = MODES[self.mode]
        lab = label.strip().upper().replace(" ", "")
        for i, (l, val) in enumerate(m["ranges"]):
            if lab in (l.upper().replace(" ", ""), val.upper()):
                self.on_range_pick(i)
                return True
        x = try_si(label)
        if x is not None:
            for i, (_, val) in enumerate(m["ranges"]):
                vv = try_si(val)
                if vv and abs(vv - x) / vv < 1e-6:
                    self.on_range_pick(i)
                    return True
        return False

    def unit(self):
        if self.unit_override:
            return self.unit_override
        if self.mode == "TEMP":
            return {"C": "°C", "F": "°F", "K": "K"}[self.cb_tunit.currentText()]
        return MODES[self.mode]["unit"]

    def noprefix(self):
        return bool(self.unit_override) or MODES[self.mode].get("noprefix", False)

    def mode_chips(self):
        chips = [MODES[self.mode]["label"]]
        if self.cb_range.count():
            chips.append(self.cb_range.currentText())
        chips.append(self.cb_speed.currentText().split(" (")[0])
        if self.sp_avg.value() > 1:
            chips.append(f"AVG {self.sp_avg.value()}")
        if self.cb_math.currentIndex():
            chips.append(self.cb_math.currentText().split(" (")[0])
        return chips

    def mode_text(self):
        return "   ".join(self.mode_chips())

    def update_mode_label(self):
        self.disp.set_chips(self.mode_chips())
        if self.cb_range.count():
            self.range_sel.set_current(max(0, self.cb_range.currentIndex()))
        self.speed_seg.set_current(self.cb_speed.currentIndex())
        u = self.unit()
        for p, ax in ((self.plot, "left"), (self.hplot, "bottom")):
            if self.noprefix():
                p.setLabel(ax, u)
            else:
                p.setLabel(ax, "", units=u)

    def set_range(self, idx):
        m = MODES[self.mode]
        if not m["ranges"]:
            return
        val = m["ranges"][idx][1]
        if self.mode == "TEMP":
            self.cmd(f"{m['cmd']} {val}")
            self.cmd(f"TEMP:RTD:UNIT {self.cb_tunit.currentText()}")
        elif val == "AUTO":
            self.cmd("AUTO")
        elif self.act_rangecmd.isChecked():
            self.cmd(f"RANGE {idx}")
        else:
            self.cmd(f"{m['cmd']} {val}")
        self.update_mode_label()

    def set_speed(self, idx):
        self.want_rate, self.rate_retry = idx, False
        letter = self.slow_letter if idx == 0 else "SMF"[idx]
        self.cmd(f"RATE {letter}")
        self.cmd("RATE?", "rate_chk")
        self.update_mode_label()

    def set_dual(self, on):
        self.cmd('FUNC2 "FREQ"' if on else 'FUNC2 "NONe"')
        if self.worker:
            self.worker.dual = on
        if not on:
            self.disp.set_secondary("")

    def set_tunit(self, _):
        self.cmd(f"TEMP:RTD:UNIT {self.cb_tunit.currentText()}")
        self.update_mode_label()

    def toggle_run(self, paused):
        self.bt_run.icon_name = "play" if paused else "pause"
        self.bt_run.update()
        if self.worker:
            self.worker.polling = not paused

    def apply_math(self):
        i = self.cb_math.currentIndex()
        self.unit_override = None
        if i == 0:
            self.cmd("CALC:STAT OFF")
        elif i == 1:
            self.cmd("CALC:FUNC NULL")
            off = try_si(self.ed_null.text())
            if off is not None:
                self.cmd(f"CALC:NULL:OFFS {off:.9E}")
        elif i in (2, 3):
            f = "DB" if i == 2 else "DBM"
            self.cmd(f"CALC:FUNC {f}")
            self.cmd(f"CALC:{f}:REF {self.cb_dbref.currentText()}")
            self.unit_override = "dB" if i == 2 else "dBm"
        else:
            self.cmd("CALC:FUNC AVER")
        self.update_mode_label()
        self.clear_data()

    def console_send(self):
        c = self.con_in.text().strip()
        if not c:
            return
        self.con_hist.append(c)
        self.con_hi = len(self.con_hist)
        self.log(f">> {c}")
        self.cmd(c, "console" if "?" in c else None)
        self.con_in.clear()

    def eventFilter(self, obj, ev):
        if obj is self.con_in and ev.type() == QtCore.QEvent.KeyPress and self.con_hist:
            if ev.key() == Qt.Key_Up:
                self.con_hi = max(0, self.con_hi - 1)
            elif ev.key() == Qt.Key_Down:
                self.con_hi = min(len(self.con_hist), self.con_hi + 1)
            else:
                return False
            self.con_in.setText(self.con_hist[self.con_hi] if self.con_hi < len(self.con_hist) else "")
            return True
        return super().eventFilter(obj, ev)

    # ================================================================== fonctions locales
    def beep(self, kind="fail"):
        if winsound:
            winsound.MessageBeep(winsound.MB_ICONHAND if kind == "fail" else winsound.MB_OK)
        else:
            QtWidgets.QApplication.beep()

    def fmt(self, v, sig=None):
        return fmt_si(v, self.unit(), sig or self.sp_dig.value(), self.noprefix())

    def copy_value(self):
        QtWidgets.QApplication.clipboard().setText(self.disp_text)

    def toggle_rel(self, on):
        self.rel = self.last_v if on and self.last_v is not None and math.isfinite(self.last_v) else None
        if on and self.rel is None:
            self.bt_rel.blockSignals(True)
            self.bt_rel.setChecked(False)
            self.bt_rel.blockSignals(False)
        self.bt_rel.setText(f"REL Δ {self.fmt(self.rel, 5)}" if self.rel is not None else "REL Δ")
        self.bt_rel.updateGeometry()
        self.clear_data()

    def clear_data(self):
        self.tbuf.clear()
        self.ybuf.clear()
        self.filt.clear()
        self.stats.reset()
        self.reset_integ()
        self.ah_buf.clear()
        self.ah_armed, self.ah_cap = True, None
        self.anom.reset()
        self.web.clear()

    def reset_integ(self):
        self.integ, self.integ_t = 0.0, None

    def reset_pf(self):
        self.npass = self.nfail = 0
        self.pf = None

    def nom_to_limits(self):
        nom, tol = try_si(self.ed_nom.text()), try_si(self.ed_tol.text())
        if nom is None or tol is None:
            return
        d = abs(nom) * tol / 100
        self.ed_lo.setText(f"{nom - d:.6g}")
        self.ed_hi.setText(f"{nom + d:.6g}")
        self.ck_lim.setChecked(True)
        self.update_limits()

    def update_limits(self, *_):
        self.lo, self.hi = try_si(self.ed_lo.text()), try_si(self.ed_hi.text())
        on = self.ck_lim.isChecked()
        for ln, val in ((self.ln_lo, self.lo), (self.ln_hi, self.hi)):
            if on and val is not None:
                ln.setValue(val)
                ln.show()
            else:
                ln.hide()
        if on and (self.lo is not None or self.hi is not None):
            f = lambda x: self.fmt(x, 4) if x is not None else "…"
            self.lb_limrange.setText(f"{f(self.lo)} … {f(self.hi)}")
        else:
            self.lb_limrange.setText("Limites désactivées")
            self.disp.set_pf(None)

    def eval_limits(self, v):
        if not self.ck_lim.isChecked() or (self.lo is None and self.hi is None):
            return None
        ok = math.isfinite(v) and (self.lo is None or v >= self.lo) and (self.hi is None or v <= self.hi)
        return "PASS" if ok else "FAIL"

    def capture(self, v, raw=None):
        if v is None:
            return
        r = self.tbl.rowCount()
        self.tbl.insertRow(r)
        st = self.eval_limits(v) or ""
        bac, dev = "", ""
        if self.ck_bin.isChecked():
            nom, d = nearest_e(abs(v), self.cb_bin.currentText())
            if nom is None:
                bac = "Hors série"
            elif abs(d) <= self.sp_bintol.value():
                bac = f"{fmt_si(nom, self.unit(), 3, self.noprefix())}"
                dev = f"{d:+.2f}"
            else:
                bac = "Hors tolérance"
                dev = f"{d:+.2f}"
            self.bins[bac] = self.bins.get(bac, 0) + 1
            self._refresh_bins()
        items = [str(r + 1), datetime.datetime.now().strftime("%H:%M:%S"), self.fmt(v),
                 f"{v:.9g}" if raw is None else f"{raw:.9g}", st, bac, dev]
        bad = st == "FAIL" or bac.startswith("Hors")
        for c, t in enumerate(items):
            it = QtWidgets.QTableWidgetItem(t)
            if st or bac:
                it.setForeground(QtGui.QColor(self.theme_t["fail"] if bad else self.theme_t["pass_"]))
            self.tbl.setItem(r, c, it)
        self.tbl.scrollToBottom()
        self.beep("fail" if bad else "ok")
        self.statusBar().showMessage(f"Capture n° {r + 1} : {self.fmt(v)} {bac}", 3000)
        tts = self.cb_tts.currentIndex()
        if tts in (2, 4):
            txt = speak_si(v, self.unit(), 4, self.noprefix())
            if bac and not bac.startswith("Hors"):
                txt += ", bac " + bac.replace("Ω", "ohms")
            elif bad:
                txt += ", hors tolérance"
            self.speaker.say(txt)

    def _refresh_bins(self):
        self.tbl_bins.setRowCount(0)

        def key(k):
            v = try_si(k.split(" ")[0])
            return (0, v) if v is not None else (1, 0)

        for k in sorted(self.bins, key=key):
            r = self.tbl_bins.rowCount()
            self.tbl_bins.insertRow(r)
            self.tbl_bins.setItem(r, 0, QtWidgets.QTableWidgetItem(k))
            self.tbl_bins.setItem(r, 1, QtWidgets.QTableWidgetItem(str(self.bins[k])))

    def clear_captures(self):
        self.tbl.setRowCount(0)
        self.bins.clear()
        self._refresh_bins()

    def autohold(self, v):
        if not self.ck_ah.isChecked():
            return
        vmin = try_si(self.ed_ahmin.text(), 0.0)
        tol_pct = self.sp_ahtol.value() / 100
        if not math.isfinite(v) or abs(v) < vmin:
            self.ah_buf.clear()
            self.ah_armed = True
            return
        if self.ah_cap is not None and abs(v - self.ah_cap) > abs(self.ah_cap) * tol_pct * 5:
            self.ah_armed = True
        self.ah_buf.append(v)
        if self.ah_armed and len(self.ah_buf) == self.ah_buf.maxlen:
            m = sum(self.ah_buf) / len(self.ah_buf)
            if all(abs(x - m) <= abs(m) * tol_pct for x in self.ah_buf):
                self.ah_cap, self.ah_armed = m, False
                self.capture(m)

    def add_event(self, kind, v, msg):
        r = self.tbl_ev.rowCount()
        self.tbl_ev.insertRow(r)
        for c, t in enumerate((datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3], kind,
                               self.fmt(v) if v is not None else "", msg)):
            self.tbl_ev.setItem(r, c, QtWidgets.QTableWidgetItem(t))
        self.tbl_ev.scrollToBottom()
        self.n_events = r + 1
        b = str(self.n_events) if self.n_events < 100 else "99+"
        self.nav_rail.set_badge("journal", b)
        self.nav_tabs.set_badge("journal", b)

    def clear_events(self):
        self.tbl_ev.setRowCount(0)
        self.n_events = 0
        self.nav_rail.set_badge("journal", "")
        self.nav_tabs.set_badge("journal", "")

    def anomaly(self, t, v):
        if not self.ck_an.isChecked():
            return
        cfg = dict(ol=self.ck_an_ol.isChecked(), low=try_si(self.ed_an_low.text()), spike=self.ck_an_spike.isChecked(),
                   k=self.sp_an_k.value(), n=self.sp_an_n.value(), drift=self.ck_an_drift.isChecked(),
                   drift_thr=self.sp_an_dthr.value(), drift_win=self.sp_an_dwin.value())
        for kind, msg in self.anom.feed(t, v, cfg):
            self.add_event(kind, v, msg)
            if self.ck_an_beep.isChecked():
                self.beep("fail")
                if self.cb_tts.currentIndex() in (3, 4):
                    self.speaker.say(f"Alerte {kind}")

    # ================================================================== mesures
    def bar_info(self, v):
        if not math.isfinite(v) or self.mode in ("TEMP", "CONT") or self.unit_override:
            return None, ""
        m = MODES[self.mode]
        idx = self.cb_range.currentIndex()
        fs = try_si(m["ranges"][idx][1]) if m["ranges"] and idx > 0 else None
        fs = fs or auto_full_scale(v)
        if not fs:
            return 0.0, ""
        return min(1.0, abs(v) / fs), fmt_si(fs, self.unit(), 3, self.noprefix()).replace(".00 ", " ").replace(".0 ", " ")

    def on_reading(self, t, v, s):
        self.rate_ts.append(t)
        if t < self.ignore_until:
            return
        raw = v
        if self.rel is not None and math.isfinite(v):
            v -= self.rel
        self.last_v, self.last_s, self.last_t = raw, s, t
        self.tbuf.append(t)
        self.ybuf.append(v)
        self.stats.add(v)
        if math.isfinite(v):
            if self.integ_t is not None:
                self.integ += v * (t - self.integ_t)
            self.integ_t = t
        pf = self.eval_limits(v)
        if pf:
            if pf == "FAIL" and self.pf != "FAIL":
                if self.ck_limbeep.isChecked():
                    self.beep("fail")
                if self.cb_tts.currentIndex() in (3, 4):
                    self.speaker.say("Hors limites, " + speak_si(v, self.unit(), 4, self.noprefix()))
            if pf != self.pf:
                self.npass += pf == "PASS"
                self.nfail += pf == "FAIL"
        self.pf = pf
        self.autohold(v)
        self.rec_process(t, v, raw, s, pf)
        self.anomaly(t, v)
        if self.battery.running and self.battery.feed(t, raw):
            self.battery_done()
        self.seq.on_reading(v)
        th = self.theme_t
        color = None
        if not self.hold:
            self.filt.append(v)
            fin = [x for x in self.filt if math.isfinite(x)]
            dv = (sum(fin) / len(fin)) if fin and len(fin) == len(self.filt) else v
            num, u = fmt_parts(dv, self.unit(), self.sp_dig.value(), self.noprefix())
            self.disp_text = f"{num} {u}"
            if self.mode == "CONT" and math.isfinite(v) and v <= try_si(self.ed_cont.text(), 50):
                color = th["warn"]
            elif pf and th["pf_color"]:
                color = th["pass_"] if pf == "PASS" else th["fail"]
            self.disp_color = color
            self.disp.set_value(num, u, color)
            self.disp.set_pf(pf)
            if s is not None:
                self.disp.set_secondary(fmt_si(s, "Hz", 6))
            frac, sc = self.bar_info(dv)
            self.disp.set_bar(frac, sc)
        self.disp.set_annunciators({
            "AUTO": self.cb_range.count() > 0 and self.cb_range.currentIndex() == 0 and self.mode != "TEMP",
            "HOLD": self.hold, "REL": self.rel is not None, "AVG": self.sp_avg.value() > 1,
            "MATH": self.cb_math.currentIndex() in (1, 4), "dB": self.cb_math.currentIndex() in (2, 3),
            "2ND": s is not None, "REC": self.rec_state == "rec", "RMT": self.worker is not None})
        sec_txt = fmt_si(s, "Hz", 6) if s is not None else ""
        if self.floating:
            self.floating.update_view(self.disp_text, self.mode_text(), sec_txt, pf, self.disp_color)
        if self.ck_web.isChecked():
            S = self.stats
            st = f"N {S.n}  min {self.fmt(S.min, 5)}  max {self.fmt(S.max, 5)}  moy {self.fmt(S.mean, 5)}" if S.n else ""
            self.web.update(t, v, display=self.disp_text, unit=self.unit(), mode=self.mode_text(), sec=sec_txt,
                            pf=pf or "", stats=st, online=True)
        if self.ck_mqtt.isChecked():
            self.mqtt.publish(t, v, self.unit(), MODES[self.mode]["label"], pf, s, self.disp_text)

    # ================================================================== enregistrement
    def _rec_clicked(self):
        self.bt_rec.toggle()
        self.act_rec.setChecked(self.bt_rec.isChecked())

    def toggle_rec(self, on):
        if on:
            fn, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Enregistrer sous", f"XDM1041_{datetime.datetime.now():%Y%m%d_%H%M%S}.csv", "CSV (*.csv)")
            if not fn:
                self.bt_rec.blockSignals(True)
                self.bt_rec.setChecked(False)
                self.bt_rec.blockSignals(False)
                self.act_rec.setChecked(False)
                return
            self.rec_file = open(fn, "w", newline="", encoding="utf-8-sig")
            self.rec_writer = csv.writer(self.rec_file, delimiter=";" if self.ck_csvfr.isChecked() else ",")
            self.rec_writer.writerow(["Horodatage", "Temps (s)", "Valeur", "Brut", "2e affichage", "Unité", "Fonction", "Limites"])
            self.rec_count, self.rec_n, self.rec_prev = 0, 0, None
            self.rec_fn = fn
            trig = self.cb_trig.currentIndex()
            if trig == 0:
                self._rec_start(time.time())
            else:
                self.rec_state = "armed"
                self.act_rec.setText("Armé")
                if trig == 3:
                    now = datetime.datetime.now()
                    tt = self.te_trig.time()
                    tgt = now.replace(hour=tt.hour(), minute=tt.minute(), second=tt.second(), microsecond=0)
                    if tgt <= now:
                        tgt += datetime.timedelta(days=1)
                    self.rec_target = tgt.timestamp()
                    self.lb_rec.setText(f"Armé : départ à {tgt:%H:%M:%S}\n{fn}")
                else:
                    self.lb_rec.setText(f"Armé : {self.cb_trig.currentText().lower()} {self.ed_trigthr.text()}\n{fn}")
            self.bt_rec.setText("Arrêter")
        else:
            if self.rec_file:
                self.rec_file.close()
            self.rec_file = self.rec_writer = None
            self.rec_state = None
            self.bt_rec.setText("Démarrer / armer")
            self.act_rec.setText("Enregistrer")
            self.lb_rec.setText(f"Terminé — {self.rec_count} lignes")
        self.act_rec.setChecked(self.bt_rec.isChecked())
        self.act_rec.updateGeometry()

    def _rec_start(self, t):
        self.rec_state = "rec"
        self.rec_t0 = t
        self.act_rec.setText("REC")
        self.lb_rec.setText(f"Enregistrement…\n{self.rec_fn}")
        if self.cb_trig.currentIndex():
            self.beep("ok")
            self.add_event("Enregistrement", self.last_v, "déclenché")

    def _n(self, v):
        if v is None:
            return ""
        s = "OL" if not math.isfinite(v) else repr(float(v))
        return s.replace(".", ",") if self.ck_csvfr.isChecked() else s

    def rec_process(self, t, v, raw, s, pf):
        if self.rec_state == "armed":
            trig, thr = self.cb_trig.currentIndex(), try_si(self.ed_trigthr.text(), 0.0)
            p = self.rec_prev
            if p is not None and math.isfinite(v) and math.isfinite(p):
                if (trig == 1 and p < thr <= v) or (trig == 2 and p > thr >= v):
                    self._rec_start(t)
            self.rec_prev = v
        if self.rec_state != "rec" or not self.rec_writer:
            return
        self.rec_n += 1
        if self.rec_n % self.sp_recdec.value() == 0:
            self.rec_writer.writerow([datetime.datetime.fromtimestamp(t).isoformat(timespec="milliseconds"),
                                      self._n(round(t - self.rec_t0, 3)), self._n(v), self._n(raw), self._n(s),
                                      self.unit(), MODES[self.mode]["label"], pf or ""])
            self.rec_count += 1
            if self.rec_count % 20 == 0:
                self.rec_file.flush()
        if self.sp_recdur.value() and t - self.rec_t0 >= self.sp_recdur.value():
            self.bt_rec.setChecked(False)

    # ================================================================== batterie / conso
    def toggle_battery(self, on):
        if on:
            val, cut = try_si(self.ed_batval.text()), try_si(self.ed_batcut.text())
            if not val:
                QtWidgets.QMessageBox.warning(self, APP, "Valeur de charge invalide.")
                self.bt_bat.setChecked(False)
                return
            if self.mode != "VDC":
                self.set_mode("VDC")
            self.battery.start(self.cb_batload.currentIndex(), val, cut)
            self.bt_bat.setText("Arrêter la décharge")
            self.add_event("Batterie", self.last_v, "début de décharge")
        else:
            self.battery.running = False
            self.bt_bat.setText("Démarrer la décharge")

    def battery_done(self):
        self.bt_bat.blockSignals(True)
        self.bt_bat.setChecked(False)
        self.bt_bat.blockSignals(False)
        self.bt_bat.setText("Démarrer la décharge")
        self.add_event("Batterie", self.last_v, f"fin : {self.battery.done_reason}")
        self.beep("fail")
        self.speaker.say("Décharge terminée, " + speak_si(self.battery.ah, "Ah", 3).replace("Ah", "ampères heure"))

    def bat_v0(self):
        if self.last_v is not None and math.isfinite(self.last_v):
            self.battery.v0 = self.last_v
            self.statusBar().showMessage(f"V à vide mémorisée : {fmt_si(self.last_v, 'V', 6)}", 5000)

    def bat_rint(self):
        self.battery.kind = self.cb_batload.currentIndex()
        self.battery.load = try_si(self.ed_batval.text(), 0.0)
        r = self.battery.measure_rint(self.last_v)
        if r is None:
            QtWidgets.QMessageBox.information(self, APP, "Mémorisez d'abord la tension à vide, puis appliquez la charge.")

    def cons_auto(self):
        thr = auto_threshold(self.view_y)
        if thr is not None:
            self.ed_cthr.setText(f"{thr:.4g}")
            self.cons_analyse()

    def cons_analyse(self):
        thr = try_si(self.ed_cthr.text())
        if thr is None or self.view_t.size < 3:
            return
        r = consumption(self.view_t, self.view_y, thr)
        self.ccurve.setData(self.view_t, np.where(np.isfinite(self.view_y), self.view_y, np.nan), connect="finite")
        self.cthr_line.setValue(thr)
        if not r:
            return
        A = lambda x: fmt_si(x, "A", 4)
        cap = try_si(self.ed_ccap.text())
        aut = ""
        if cap and r["avg"] > 0:
            hh = cap / 1000 / r["avg"]
            aut = f"\nAutonomie estimée : {hh:,.1f} h  ({hh / 24:,.1f} j)".replace(",", " ")
        per = f"{r['period']:.2f} s" if math.isfinite(r["period"]) else "—"
        wd = f"{r['wake_dur'] * 1000:.1f} ms" if math.isfinite(r["wake_dur"]) else "—"
        self.lb_cons.setText(
            f"Durée analysée : {r['duration']:.1f} s\nCourant moyen  : {A(r['avg'])}\nVeille moyenne : {A(r['sleep'])}\n"
            f"Réveil moyen   : {A(r['wake'])}\nCrête          : {A(r['peak'])}\nRapport cyclique : {r['duty']:.3f} %\n"
            f"Réveils : {r['wakes']}   période : {per}\nDurée réveil moy. : {wd}{aut}")

    # ================================================================== graphique
    def toggle_region(self, on):
        if on and self.view_t.size > 1:
            t0, t1 = self.view_t[0], self.view_t[-1]
            self.region.setRegion((t0 + (t1 - t0) * 0.5, t1))
        self.region.setVisible(on)
        self.lb_region.setVisible(on)
        self.lb_region.setText("")
        if on:
            self.ck_chartpause.setChecked(True)
            self.update_region()

    def update_region(self):
        if not self.ck_region.isChecked() or self.view_t.size < 2:
            return
        a, b = self.region.getRegion()
        m = (self.view_t >= a) & (self.view_t <= b) & np.isfinite(self.view_y)
        t, y = self.view_t[m], self.view_y[m]
        if t.size < 2:
            self.lb_region.setText("Région vide")
            return
        f = lambda x: self.fmt(x, 6)
        dv = y[-1] - y[0]
        dt = t[-1] - t[0]
        slope = np.polyfit(t - t[0], y, 1)[0] if t.size > 2 else (dv / dt if dt else 0)
        self.lb_region.setText(
            f"Δt {dt:.3f} s   Δ {f(dv)}   pente {f(slope)}/s ({f(slope * 60)}/min)   N {t.size}   "
            f"min {f(y.min())}   max {f(y.max())}   moy {f(y.mean())}   σ {f(y.std(ddof=1) if y.size > 1 else 0)}")

    def autoscale(self):
        self.plot.enableAutoRange()

    def refresh_views(self):
        now = time.time()
        while self.rate_ts and now - self.rate_ts[0] > 2:
            self.rate_ts.popleft()
        self.disp.set_rate(f"{len(self.rate_ts) / 2:.1f} mes/s".replace(".", ",") if self.worker else "")
        u, npf, dg = self.unit(), self.noprefix(), self.sp_dig.value()
        f = lambda x: fmt_si(x, u, dg, npf)
        S = self.stats
        if S.n:
            for k, val in (("N", f"{S.n:,}".replace(",", " ")), ("Min", f(S.min)), ("Max", f(S.max)), ("Moyenne", f(S.mean)),
                           ("σ", f(S.std)), ("Crête-crête", f(S.max - S.min)),
                           ("σ / moy", f"{100 * S.std / abs(S.mean):.4f} %" if S.mean else "—")):
                self.st[k].setText(val)
            if self.theme_t["skin"] == "instrument":
                g = lambda x: fmt_si(x, u, 6, npf)
                self.disp.set_statsline(f"MIN {g(S.min)}    MOY {g(S.mean)}    MAX {g(S.max)}    σ {fmt_si(S.std, u, 3, npf)}")
        else:
            for k in self.st:
                self.st[k].setText("—")
            self.disp.set_statsline("")
        self.st_pf.setText(f"PASS {self.npass} · FAIL {self.nfail}")
        extra = []
        if self.rel is not None:
            extra.append(f"REL réf. {self.fmt(self.rel, 5)}")
        if self.ck_bin.isChecked() and self.last_v is not None and math.isfinite(self.last_v) and self.mode in ("RES", "CAP"):
            nom, d = nearest_e(abs(self.last_v), self.cb_bin.currentText())
            if nom:
                extra.append(f"Bac {self.cb_bin.currentText()} : {fmt_si(nom, u, 3, npf)} · écart {d:+.2f} %")
        self.disp.set_extra("   ·   ".join(extra))
        h = self.integ / 3600
        if self.mode in ("IDC", "IAC"):
            volt = try_si(self.ed_volt.text(), 0.0)
            self.lb_integ.setText(f"Q = {fmt_si(h, 'Ah', 6)}\n    ({fmt_si(self.integ, 'C', 6)})\nE ≈ {fmt_si(h * volt, 'Wh', 6)} @ {volt:g} V")
        else:
            self.lb_integ.setText(f"∫ = {fmt_si(self.integ, u + '·s', 6, npf)}\n  = {fmt_si(h, u + '·h', 6, npf)}")
        if self.rec_state == "armed" and self.cb_trig.currentIndex() == 3 and self.rec_target and now >= self.rec_target:
            self._rec_start(now)
        if self.rec_state == "rec" and self.sp_recdur.value() and now - self.rec_t0 >= self.sp_recdur.value():
            self.bt_rec.setChecked(False)
        if self.cb_tts.currentIndex() == 1 and self.worker and now - self.tts_last >= self.sp_tts.value() and self.last_v is not None:
            self.tts_last = now
            self.speaker.say(speak_si(self.last_v - (self.rel or 0), u, 4, npf))
        B = self.battery
        if B.pts and (B.running or self.section == "batt"):
            a = np.array(B.pts)
            self.bcurve.setData(a[:, 1], a[:, 2])
            el = a[-1, 0]
            rint = f"{B.rint * 1000:.1f} mΩ" if B.rint is not None else "—"
            v0 = fmt_si(B.v0, "V", 5) if B.v0 is not None else "—"
            self.lb_bat.setText(
                f"{'En cours' if B.running else 'Arrêtée'}{' — ' + B.done_reason if B.done_reason else ''}\n"
                f"Durée     : {int(el // 3600)} h {int(el % 3600 // 60):02d} min {int(el % 60):02d} s\n"
                f"Capacité  : {fmt_si(B.ah, 'Ah', 5)}\nÉnergie   : {fmt_si(B.wh, 'Wh', 5)}\n"
                f"Tension   : {fmt_si(a[-1, 2], 'V', 6)}  (début {fmt_si(a[0, 2], 'V', 5)})\n"
                f"Courant   : {fmt_si(B.current(a[-1, 2]), 'A', 5)}\nV à vide  : {v0}\nR interne : {rint}")
        elif B.rint is not None or B.v0 is not None:
            self.lb_bat.setText(f"V à vide  : {fmt_si(B.v0, 'V', 5) if B.v0 is not None else '—'}\n"
                                f"R interne : {f'{B.rint * 1000:.1f} mΩ' if B.rint is not None else '—'}")
        if self.ck_chartpause.isChecked() or not self.tbuf:
            if self.ck_region.isChecked():
                self.update_region()
            return
        t = np.fromiter(self.tbuf, float, len(self.tbuf))
        y = np.fromiter(self.ybuf, float, len(self.ybuf))
        win = [30, 60, 300, 900, 3600, None][self.cb_win.currentIndex()]
        if win:
            i0 = np.searchsorted(t, t[-1] - win)
            t, y = t[i0:], y[i0:]
        self.view_t, self.view_y = t, y
        yp = np.where(np.isfinite(y), y, np.nan)
        if self.section == "mesure":
            self.curve.setData(t, yp, connect="finite")
            if self.ck_mean.isChecked() and S.n:
                self.ln_mean.setValue(S.mean)
                self.ln_mean.show()
            else:
                self.ln_mean.hide()
            if self.chart_stack.currentIndex() == 1:
                self._draw_hist(y)
        elif self.section == "conso":
            self.ccurve.setData(t, yp, connect="finite")
            if self.ck_clive.isChecked() and int(now * 2) % 4 == 0:
                self.cons_analyse()

    def _draw_hist(self, y):
        yf = y[np.isfinite(y)]
        self.hplot.clear()
        if yf.size > 1 and yf.max() > yf.min():
            cnt, edges = np.histogram(yf, bins=self.sp_bins.value())
            c = QtGui.QColor(self.theme_t["curve"])
            c.setAlpha(140)
            self.hplot.addItem(pg.BarGraphItem(x0=edges[:-1], x1=edges[1:], height=cnt, brush=c, pen=self.theme_t["curve"]))

    # ================================================================== comparaison
    def _read_trace(self, path):
        with open(path, encoding="utf-8-sig") as fh:
            txt = fh.read()
        delim = ";" if txt.count(";") > txt.count(",") else ","
        rows = list(csv.reader(io.StringIO(txt), delimiter=delim))
        hdr = [h.strip().lower() for h in rows[0]]
        idx = lambda pre: next((i for i, h in enumerate(hdr) if h.startswith(pre)), None)
        ti, vi, hi = idx("temps"), idx("valeur"), idx("horodatage")
        if vi is None:
            raise ValueError("colonne « Valeur » introuvable")

        def num(s):
            s = s.strip()
            if delim == ";":
                s = s.replace(",", ".")
            try:
                return float(s)
            except ValueError:
                return math.nan

        t, y = [], []
        for r in rows[1:]:
            if len(r) <= vi:
                continue
            if ti is not None:
                tv = num(r[ti])
            elif hi is not None:
                try:
                    tv = datetime.datetime.fromisoformat(r[hi]).timestamp()
                except ValueError:
                    continue
            else:
                tv = len(t)
            t.append(tv)
            y.append(num(r[vi]))
        t = np.array(t)
        return t - t[0], np.array(y)

    def load_traces(self):
        fns, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Charger des enregistrements", "", "CSV (*.csv)")
        for fn in fns:
            try:
                t, y = self._read_trace(fn)
            except (OSError, ValueError, IndexError) as e:
                QtWidgets.QMessageBox.warning(self, APP, f"{os.path.basename(fn)} : {e}")
                continue
            self._add_trace(os.path.basename(fn), t, y)

    def add_current_trace(self):
        if len(self.tbuf) < 2:
            return
        t = np.fromiter(self.tbuf, float)
        self._add_trace(f"Session {datetime.datetime.now():%H:%M:%S} ({MODES[self.mode]['label']})", t - t[0], np.fromiter(self.ybuf, float))

    def _add_trace(self, name, t, y):
        self.traces.append((name, t, y))
        it = QtWidgets.QListWidgetItem(name)
        it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
        it.setCheckState(Qt.Checked)
        it.setForeground(QtGui.QColor(TRACE_COLORS[(len(self.traces) - 1) % len(TRACE_COLORS)]))
        self.lw_traces.addItem(it)
        self.goto("analyse")
        self.draw_compare()

    def remove_trace(self):
        r = self.lw_traces.currentRow()
        if r >= 0:
            self.lw_traces.takeItem(r)
            self.traces.pop(r)
            self.draw_compare()

    def draw_compare(self):
        self.cmpplot.clear()
        if self.cmpplot.plotItem.legend:
            self.cmpplot.plotItem.legend.clear()
        for i, (name, t, y) in enumerate(self.traces):
            if i < self.lw_traces.count() and self.lw_traces.item(i).checkState() == Qt.Checked:
                self.cmpplot.plot(t, np.where(np.isfinite(y), y, np.nan), connect="finite", name=name,
                                  pen=pg.mkPen(TRACE_COLORS[i % len(TRACE_COLORS)], width=1.5))

    # ================================================================== séquences
    def seq_run(self):
        if not self.worker:
            QtWidgets.QMessageBox.warning(self, APP, "Connectez le multimètre avant de lancer une séquence.")
            return
        if self.bt_run.isChecked():
            self.bt_run.setChecked(False)
        self.seq_out.clear()
        self.lb_verdict.setText("…")
        self.lb_verdict.setStyleSheet("")
        self.seq.start(self.ed_seq.toPlainText())

    def seq_done(self, verdict):
        col = {"PASS": self.theme_t["pass_"], "FAIL": self.theme_t["fail"]}.get(verdict, self.theme_t["warn"])
        self.lb_verdict.setText(verdict)
        self.lb_verdict.setStyleSheet(f"color:{col};")
        self.add_event("Séquence", None, f"verdict {verdict}")
        if verdict in ("PASS", "FAIL"):
            self.beep("ok" if verdict == "PASS" else "fail")

    def seq_load(self):
        fn, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Ouvrir une séquence", "", "Séquence (*.xdmseq *.txt)")
        if fn:
            with open(fn, encoding="utf-8") as f:
                self.ed_seq.setPlainText(f.read())

    def seq_save(self):
        fn, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Enregistrer la séquence", "test.xdmseq", "Séquence (*.xdmseq)")
        if fn:
            with open(fn, "w", encoding="utf-8") as f:
                f.write(self.ed_seq.toPlainText())

    # ================================================================== services
    def toggle_web(self, on):
        if on:
            try:
                self.web.start(self.sp_webport.value())
            except OSError as e:
                self.lb_web.setText(f"Erreur : {e}")
                self.ck_web.blockSignals(True)
                self.ck_web.setChecked(False)
                self.ck_web.blockSignals(False)
                return
            url = f"http://{WebServer.local_ip()}:{self.sp_webport.value()}/"
            self.lb_web.setText(f'<a href="{url}">{url}</a>')
            self.web.set(online=bool(self.worker), unit=self.unit(), mode=self.mode_text())
            self.sp_webport.setEnabled(False)
        else:
            self.web.stop()
            self.lb_web.setText("")
            self.sp_webport.setEnabled(True)

    def mqtt_cfg(self):
        c = self.cfg
        return dict(host=c.value("mqtt_host", "homeassistant.local"), port=int(c.value("mqtt_port", 1883)),
                    user=c.value("mqtt_user", ""), password=c.value("mqtt_pass", ""), prefix=c.value("mqtt_prefix", "xdm1041"),
                    discovery=str(c.value("mqtt_disc", "true")).lower() == "true", interval=float(c.value("mqtt_int", 1.0)),
                    discovery_prefix=c.value("mqtt_dprefix", "homeassistant"))

    def mqtt_dialog(self):
        c = self.mqtt_cfg()
        d = QtWidgets.QDialog(self)
        d.setWindowTitle("Configuration MQTT")
        fl = QtWidgets.QFormLayout(d)
        fl.setContentsMargins(20, 20, 20, 20)
        fl.setVerticalSpacing(10)
        host = QtWidgets.QLineEdit(c["host"])
        port = QtWidgets.QSpinBox(minimum=1, maximum=65535, value=c["port"])
        user = QtWidgets.QLineEdit(c["user"])
        pw = QtWidgets.QLineEdit(c["password"], echoMode=QtWidgets.QLineEdit.Password)
        pre = QtWidgets.QLineEdit(c["prefix"])
        disc = QtWidgets.QCheckBox("Découverte automatique Home Assistant", checked=c["discovery"])
        dpre = QtWidgets.QLineEdit(c["discovery_prefix"])
        itv = QtWidgets.QDoubleSpinBox(decimals=1, minimum=0.1, maximum=3600, value=c["interval"], suffix=" s")
        for lab, w in (("Serveur", host), ("Port", port), ("Utilisateur", user), ("Mot de passe", pw), ("Préfixe des topics", pre),
                       ("", disc), ("Préfixe découverte", dpre), ("Intervalle de publication", itv)):
            fl.addRow(lab, w)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        fl.addRow(bb)
        if d.exec() != QtWidgets.QDialog.Accepted:
            return
        for k, v in (("mqtt_host", host.text().strip()), ("mqtt_port", port.value()), ("mqtt_user", user.text()),
                     ("mqtt_pass", pw.text()), ("mqtt_prefix", pre.text().strip() or "xdm1041"),
                     ("mqtt_disc", "true" if disc.isChecked() else "false"), ("mqtt_dprefix", dpre.text().strip() or "homeassistant"),
                     ("mqtt_int", itv.value())):
            self.cfg.setValue(k, v)
        if self.ck_mqtt.isChecked():
            self.toggle_mqtt(True)

    def toggle_mqtt(self, on):
        if on:
            sid = self.idn.split(",")[2] if self.idn.count(",") >= 2 else "dmm"
            self.lb_mqtt.setText("Connexion MQTT…")
            if not self.mqtt.start(self.mqtt_cfg(), sid):
                self.ck_mqtt.blockSignals(True)
                self.ck_mqtt.setChecked(False)
                self.ck_mqtt.blockSignals(False)
        else:
            self.mqtt.stop()
            self.lb_mqtt.setText("")

    # ================================================================== export
    def _ask_save(self, title, default, filt):
        fn, _ = QtWidgets.QFileDialog.getSaveFileName(self, title, default, filt)
        return fn

    def export_buffer(self):
        if not self.tbuf:
            return
        fn = self._ask_save("Exporter", f"XDM1041_{datetime.datetime.now():%Y%m%d_%H%M%S}.csv", "CSV (*.csv)")
        if not fn:
            return
        t0 = self.tbuf[0]
        with open(fn, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh, delimiter=";" if self.ck_csvfr.isChecked() else ",")
            w.writerow(["Horodatage", "Temps (s)", "Valeur", "Unité"])
            for t, v in zip(self.tbuf, self.ybuf):
                w.writerow([datetime.datetime.fromtimestamp(t).isoformat(timespec="milliseconds"), self._n(round(t - t0, 3)), self._n(v), self.unit()])
        self.statusBar().showMessage(f"Exporté : {fn}", 5000)

    def _table_rows(self, tbl):
        return [[tbl.item(r, c).text() if tbl.item(r, c) else "" for c in range(tbl.columnCount())] for r in range(tbl.rowCount())]

    def _headers(self, tbl):
        return [tbl.horizontalHeaderItem(c).text() for c in range(tbl.columnCount())]

    def _export_table(self, tbl, default):
        fn = self._ask_save("Exporter", default, "CSV (*.csv)")
        if not fn:
            return
        with open(fn, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh, delimiter=";" if self.ck_csvfr.isChecked() else ",")
            w.writerow(self._headers(tbl))
            w.writerows(self._table_rows(tbl))

    def export_captures(self):
        self._export_table(self.tbl, "captures.csv")

    def _stats_rows(self):
        S = self.stats
        f = lambda x: self.fmt(x)
        rows = [("Fonction", MODES[self.mode]["label"]), ("Gamme", self.cb_range.currentText()), ("Unité", self.unit())]
        if S.n:
            rows += [("N", S.n), ("Minimum", f(S.min)), ("Maximum", f(S.max)), ("Moyenne", f(S.mean)), ("Écart-type", f(S.std)),
                     ("Crête-crête", f(S.max - S.min))]
        if self.tbuf:
            rows += [("Début", datetime.datetime.fromtimestamp(self.tbuf[0]).strftime("%d/%m/%Y %H:%M:%S")),
                     ("Fin", datetime.datetime.fromtimestamp(self.tbuf[-1]).strftime("%d/%m/%Y %H:%M:%S"))]
        if self.ck_lim.isChecked():
            rows += [("Limites", f"{self.ed_lo.text()} … {self.ed_hi.text()}"), ("PASS / FAIL", f"{self.npass} / {self.nfail}")]
        return rows

    def export_excel(self):
        if not self.tbuf:
            QtWidgets.QMessageBox.information(self, APP, "Aucune donnée à exporter.")
            return
        fn = self._ask_save("Exporter vers Excel", f"XDM1041_{datetime.datetime.now():%Y%m%d_%H%M%S}.xlsx", "Excel (*.xlsx)")
        if not fn:
            return
        try:
            export_xlsx(fn, list(self.tbuf), list(self.ybuf), self.unit(), MODES[self.mode]["label"], self._stats_rows(),
                        self._table_rows(self.tbl), self._headers(self.tbl), self._table_rows(self.tbl_ev), self._headers(self.tbl_ev))
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, APP, f"Export Excel impossible : {e}")
            return
        self.statusBar().showMessage(f"Exporté : {fn}", 5000)

    def _grab(self, plot, width=1600):
        ex = pyqtgraph.exporters.ImageExporter(plot.plotItem)
        ex.parameters()["width"] = width
        return ex.export(toBytes=True)

    def export_png(self):
        fn = self._ask_save("Exporter graphique", "XDM1041_graph.png", "PNG (*.png)")
        if fn:
            ex = pyqtgraph.exporters.ImageExporter(self.plot.plotItem)
            ex.parameters()["width"] = 1920
            ex.export(fn)

    def report_pdf(self):
        d = QtWidgets.QDialog(self)
        d.setWindowTitle("Rapport d'intervention")
        fl = QtWidgets.QFormLayout(d)
        fl.setContentsMargins(20, 20, 20, 20)
        fl.setVerticalSpacing(10)
        c = self.cfg
        ed = {k: QtWidgets.QLineEdit(c.value(f"rep_{k}", "") if k in ("tech", "company") else "") for k in
              ("company", "client", "device", "ref", "tech")}
        notes = QtWidgets.QPlainTextEdit()
        notes.setMinimumHeight(110)
        checks = {k: QtWidgets.QCheckBox(lab, checked=True) for k, lab in (
            ("chart", "Graphique"), ("hist", "Histogramme"), ("stats", "Statistiques"), ("caps", "Captures / tri"),
            ("events", "Événements"), ("bat", "Batterie"), ("cons", "Consommation"), ("seq", "Journal de séquence"))}
        for k, lab in (("company", "Entreprise"), ("client", "Client"), ("device", "Appareil"), ("ref", "N° d'intervention"),
                       ("tech", "Technicien")):
            fl.addRow(lab, ed[k])
        fl.addRow("Notes / diagnostic", notes)
        grid = QtWidgets.QGridLayout()
        for i, cb in enumerate(checks.values()):
            grid.addWidget(cb, i // 4, i % 4)
        fl.addRow(grid)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        fl.addRow(bb)
        if d.exec() != QtWidgets.QDialog.Accepted:
            return
        c.setValue("rep_tech", ed["tech"].text())
        c.setValue("rep_company", ed["company"].text())
        safe = "".join(ch for ch in (ed["ref"].text() or ed["client"].text() or "rapport") if ch.isalnum() or ch in "-_ ")
        fn = self._ask_save("Enregistrer le rapport", f"Rapport_{safe}_{datetime.datetime.now():%Y%m%d}.pdf", "PDF (*.pdf)")
        if not fn:
            return
        sections, images = [], {}
        on = lambda k: checks[k].isChecked()
        if on("stats"):
            sections.append(("Mesures", table_html(["Paramètre", "Valeur"], self._stats_rows())))
        if on("chart") and self.tbuf:
            images["chart.png"] = self._grab(self.plot)
            sections.append(("Graphique", "<img src='chart.png' width='640'>"))
        if on("hist") and self.tbuf:
            self._draw_hist(self.view_y if self.view_y.size else np.fromiter(self.ybuf, float))
            images["hist.png"] = self._grab(self.hplot)
            sections.append(("Histogramme", "<img src='hist.png' width='640'>"))
        if on("caps") and self.tbl.rowCount():
            sections.append(("Captures", table_html(self._headers(self.tbl), self._table_rows(self.tbl))))
            if self.bins:
                sections.append(("Répartition par bac", table_html(["Bac", "Nombre"], self._table_rows(self.tbl_bins))))
        if on("events") and self.tbl_ev.rowCount():
            sections.append(("Événements", table_html(self._headers(self.tbl_ev), self._table_rows(self.tbl_ev))))
        if on("bat") and self.battery.pts:
            images["bat.png"] = self._grab(self.bplot)
            sections.append(("Décharge batterie", f"<pre>{self.lb_bat.text()}</pre><img src='bat.png' width='640'>"))
        if on("cons") and "Courant moyen" in self.lb_cons.text():
            sections.append(("Consommation", f"<pre>{self.lb_cons.text()}</pre>"))
        if on("seq") and self.seq_out.toPlainText():
            sections.append((f"Séquence de test — verdict {self.lb_verdict.text()}", f"<pre>{self.seq_out.toPlainText()}</pre>"))
        info = dict(title="Rapport d'intervention" + (f" — {ed['company'].text()}" if ed["company"].text() else ""),
                    soft=f"{APP} {VERSION} — {self.idn}",
                    fields=[("Client", ed["client"].text()), ("Appareil", ed["device"].text()), ("N° d'intervention", ed["ref"].text()),
                            ("Technicien", ed["tech"].text()), ("Date", datetime.datetime.now().strftime("%d/%m/%Y %H:%M")),
                            ("Instrument", self.idn or "OWON XDM1041")],
                    notes=notes.toPlainText())
        try:
            build_pdf(fn, info, sections, images)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, APP, f"Rapport impossible : {e}")
            return
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(fn))

    # ================================================================== affichage
    def toggle_top(self):
        self.setWindowFlag(Qt.WindowStaysOnTopHint, self.act_top.isChecked())
        self.show()

    def toggle_floating(self):
        if self.act_float.isChecked():
            self.floating = FloatingDisplay()
            self.floating.closed.connect(self._floating_closed)
            g = self.cfg.value("float_pos")
            if g:
                self.floating.move(g)
            self.floating.update_view(self.disp_text, self.mode_text(), "", self.pf, self.disp_color)
            self.floating.show()
        elif self.floating:
            self.floating.close()

    def _floating_closed(self):
        if self.floating:
            self.cfg.setValue("float_pos", self.floating.pos())
        self.floating = None
        self.act_float.setChecked(False)

    def closeEvent(self, ev):
        self._save_settings()
        if self.rec_file:
            self.rec_file.close()
        self.seq.stop(silent=True)
        self.disconnect_dmm()
        self.mqtt.stop()
        self.web.stop()
        if self.floating:
            self.floating.close()
        super().closeEvent(ev)


def main():
    QtWidgets.QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("XDM1041Studio")
    app.setOrganizationName("Davy")
    app.setWindowIcon(app_icon())
    app.setStyle("Fusion")
    TH.load_fonts()
    w = Main()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
