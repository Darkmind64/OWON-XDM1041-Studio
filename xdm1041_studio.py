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

from xdmlib.core import (APP, VERSION, MODES, FUNC_MAP, DB_REFS, fmt_si, speak_si, parse_si, try_si, Stats, DmmWorker)
from xdmlib.analysis import SERIES, nearest_e, auto_threshold, consumption, AnomalyDetector, Battery
from xdmlib.services import WebServer, MqttPublisher, Speaker
from xdmlib.reports import build_pdf, table_html, export_xlsx
from xdmlib.sequence import SeqRunner, EXAMPLE, HELP
from xdmlib.widgets import THEMES, app_icon, apply_theme, FloatingDisplay

try:
    import winsound
except ImportError:
    winsound = None

MAXBUF = 300_000
TRACE_COLORS = ["#4cff7a", "#ffb74c", "#7ad7ff", "#ff6fb5", "#c8a2ff", "#ffe066", "#ff5050", "#5cf2d6"]
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


class Main(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP} {VERSION}")
        self.setWindowIcon(app_icon())
        self.resize(1500, 950)
        self.cfg = QtCore.QSettings("Davy", "XDM1041Studio")
        self.theme = self.cfg.value("theme", "dark")
        self.colors = apply_theme(QtWidgets.QApplication.instance(), self.theme)
        self.worker = None
        self.mode = "VDC"
        self.unit_override = None
        self.hold = False
        self.rel = None
        self.last_v = self.last_s = None
        self.last_t = 0.0
        self.idn = ""
        self.disp_text = "-----"
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
        self.web = WebServer()
        self.mqtt = MqttPublisher()
        self.mqtt.status.connect(lambda s: self.lb_mqtt.setText(s))
        self.speaker = Speaker()
        self.floating = None
        self.seq = SeqRunner(self)
        self._build()
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
        self.timer = QtCore.QTimer(self, interval=150, timeout=self.refresh_views)
        self.timer.start()

    # ================================================================== construction UI
    def R(self, name, w):
        self.persist[name] = w
        return w

    def _group(self, lv, title):
        g = QtWidgets.QGroupBox(title)
        lv.addWidget(g)
        return QtWidgets.QFormLayout(g)

    def _plot(self, **kw):
        p = pg.PlotWidget(**kw)
        p.showGrid(x=True, y=True, alpha=0.3)
        self.plots.append(p)
        return p

    def _build(self):
        self._menus()
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        top = QtWidgets.QHBoxLayout()
        self.cb_port = QtWidgets.QComboBox(minimumWidth=280)
        self.bt_refresh = QtWidgets.QPushButton("↻", clicked=self.refresh_ports, maximumWidth=34)
        self.bt_detect = QtWidgets.QPushButton("Détecter", clicked=self.detect_port)
        self.bt_conn = QtWidgets.QPushButton("Connecter", clicked=self.toggle_connect, minimumWidth=110)
        self.lb_idn = QtWidgets.QLabel("Déconnecté")
        self.lb_rate = QtWidgets.QLabel("")
        self.cb_profile = QtWidgets.QComboBox(minimumWidth=180)
        self.cb_profile.activated.connect(lambda i: self.load_profile(self.cb_profile.itemText(i)) if i > 0 else None)
        for w in (QtWidgets.QLabel("Port :"), self.cb_port, self.bt_refresh, self.bt_detect, self.bt_conn, self.lb_idn):
            top.addWidget(w)
        top.addStretch()
        top.addWidget(QtWidgets.QLabel("Profil :"))
        top.addWidget(self.cb_profile)
        top.addWidget(self.lb_rate)
        root.addLayout(top)

        split = QtWidgets.QSplitter(Qt.Horizontal)
        root.addWidget(split, 1)
        self.left = QtWidgets.QScrollArea(widgetResizable=True, minimumWidth=400, maximumWidth=480)
        self.left.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lw = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(lw)
        self.left.setWidget(lw)
        split.addWidget(self.left)
        self._build_left(lv)

        right = QtWidgets.QWidget()
        rv = QtWidgets.QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        self._build_display(rv)
        self.tabs = QtWidgets.QTabWidget()
        rv.addWidget(self.tabs, 1)
        self._tab_chart()
        self._tab_hist()
        self._tab_captures()
        self._tab_events()
        self._tab_battery()
        self._tab_conso()
        self._tab_compare()
        self._tab_seq()
        self._tab_console()
        self.statusBar().showMessage("Prêt")

    def _menus(self):
        mb = self.menuBar()
        mf = mb.addMenu("&Fichier")
        self.menu_prof = mf.addMenu("Profils")
        mf.addSeparator()
        mf.addAction("Exporter les données (CSV)…", self.export_buffer, "Ctrl+E")
        mf.addAction("Exporter vers Excel (.xlsx)…", self.export_excel, "Ctrl+Shift+E")
        mf.addAction("Exporter le graphique (PNG)…", self.export_png)
        mf.addAction("Rapport d'intervention (PDF)…", self.report_pdf, "Ctrl+P")
        mf.addSeparator()
        mf.addAction("Quitter", self.close, "Ctrl+Q")
        mv = mb.addMenu("&Affichage")
        self.act_top = mv.addAction("Toujours au premier plan", self.toggle_top)
        self.act_top.setCheckable(True)
        self.act_compact = mv.addAction("Mode compact", self.toggle_compact)
        self.act_compact.setCheckable(True)
        self.act_float = mv.addAction("Afficheur flottant", self.toggle_floating, "F11")
        self.act_float.setCheckable(True)
        mv.addSeparator()
        self.act_light = mv.addAction("Thème clair", self.toggle_theme)
        self.act_light.setCheckable(True)
        mz = mv.addMenu("Taille du texte")
        self.zoom_group = QtGui.QActionGroup(self)
        for pct in (100, 115, 130, 150, 175):
            a = mz.addAction(f"{pct} %", lambda p=pct: self.set_zoom(p))
            a.setCheckable(True)
            a.setData(pct)
            self.zoom_group.addAction(a)
        mo = mb.addMenu("&Options")
        self.act_autorec = mo.addAction("Reconnexion automatique")
        self.act_fast = mo.addAction("Vitesse rapide à la connexion")
        self.act_restore = mo.addAction("Appliquer le dernier mode à la connexion")
        self.act_rangecmd = mo.addAction("Changer de gamme par RANGE (sans réinitialiser)")
        for a in (self.act_autorec, self.act_fast, self.act_restore, self.act_rangecmd):
            a.setCheckable(True)
            a.setChecked(True)
        mo.addSeparator()
        mo.addAction("Configuration MQTT…", self.mqtt_dialog)
        mh = mb.addMenu("&Aide")
        mh.addAction("Raccourcis clavier", lambda: QtWidgets.QMessageBox.information(self, "Raccourcis", (
            "H  HOLD\nR  REL Δ\nEspace  capture\nP  pause / reprise acquisition\nC  copier la valeur\n"
            "A  auto-échelle du graphique\nF11  afficheur flottant\nCtrl+E  export CSV\nCtrl+Shift+E  export Excel\n"
            "Ctrl+P  rapport PDF\nCtrl+Q  quitter")))
        mh.addAction("Langage des séquences", lambda: QtWidgets.QMessageBox.information(self, "Séquences", HELP))
        mh.addAction("À propos", lambda: QtWidgets.QMessageBox.about(
            self, APP, f"{APP} {VERSION}\nSCPI OWON XDM1041 — USB COM 115200 8N1\nPython / PySide6 / pyqtgraph"))

    def _build_left(self, lv):
        g = QtWidgets.QGroupBox("Fonction")
        gl = QtWidgets.QGridLayout(g)
        self.fbtn = QtWidgets.QButtonGroup(self)
        for i, (k, m) in enumerate(MODES.items()):
            b = QtWidgets.QPushButton(m["label"], checkable=True, minimumWidth=60)
            b.clicked.connect(lambda _=False, key=k: self.set_mode(key))
            b.setProperty("key", k)
            self.fbtn.addButton(b)
            gl.addWidget(b, i // 4, i % 4)
        lv.addWidget(g)

        fl = self._group(lv, "Acquisition")
        self.cb_range = self.R("range", QtWidgets.QComboBox())
        self.cb_range.activated.connect(self.set_range)
        self.cb_speed = self.R("speed", QtWidgets.QComboBox())
        self.cb_speed.addItems(["Lente (S)", "Moyenne (M)", "Rapide (F)"])
        self.cb_speed.activated.connect(self.set_speed)
        self.sp_int = self.R("interval", QtWidgets.QSpinBox(suffix=" ms", maximum=3_600_000, singleStep=100))
        self.sp_int.valueChanged.connect(lambda v: self.worker and setattr(self.worker, "interval", v / 1000))
        self.ck_dual = self.R("dual", QtWidgets.QCheckBox("2ᵉ affichage : fréquence (AC)"))
        self.ck_dual.toggled.connect(self.set_dual)
        self.bt_run = QtWidgets.QPushButton("⏸ Pause acquisition", checkable=True, toggled=self.toggle_run)
        fl.addRow("Gamme", self.cb_range)
        fl.addRow("Vitesse", self.cb_speed)
        fl.addRow("Intervalle", self.sp_int)
        fl.addRow(self.ck_dual)
        fl.addRow(self.bt_run)

        fl = self._group(lv, "Affichage")
        self.sp_dig = self.R("digits", QtWidgets.QSpinBox(minimum=3, maximum=8, value=6))
        self.sp_avg = self.R("avg", QtWidgets.QSpinBox(minimum=1, maximum=500, value=1))
        self.sp_avg.valueChanged.connect(lambda n: setattr(self, "filt", deque(self.filt, maxlen=n)))
        fl.addRow("Chiffres significatifs", self.sp_dig)
        fl.addRow("Moyenne glissante (N)", self.sp_avg)

        fl = self._group(lv, "Maths de l'appareil")
        self.cb_math = QtWidgets.QComboBox()
        self.cb_math.addItems(["Aucune", "NULL (relatif)", "dB", "dBm", "Statistiques"])
        self.cb_dbref = QtWidgets.QComboBox()
        self.cb_dbref.addItems(DB_REFS)
        self.cb_dbref.setCurrentText("600")
        self.ed_null = QtWidgets.QLineEdit(placeholderText="offset ex. 12m")
        hb = QtWidgets.QHBoxLayout()
        hb.addWidget(QtWidgets.QPushButton("Appliquer", clicked=self.apply_math))
        hb.addWidget(QtWidgets.QPushButton("Lire stats DMM", clicked=lambda: self.cmd("CALC:AVER:ALL?", "calc_all")))
        fl.addRow("Fonction", self.cb_math)
        fl.addRow("Réf. dB/dBm (Ω)", self.cb_dbref)
        fl.addRow("Offset NULL", self.ed_null)
        fl.addRow(hb)

        fl = self._group(lv, "Réglages appareil")
        self.cb_tunit = self.R("tunit", QtWidgets.QComboBox())
        self.cb_tunit.addItems(["C", "F", "K"])
        self.cb_tunit.activated.connect(self.set_tunit)
        self.ed_cont = self.R("cont", QtWidgets.QLineEdit("50", maximumWidth=80))
        self.ed_cont.editingFinished.connect(lambda: self.cmd(f"CONT:THRE {self.ed_cont.text().strip()}"))
        self.ck_beep = QtWidgets.QCheckBox("Buzzer DMM", checked=True)
        self.ck_beep.toggled.connect(lambda on: self.cmd(f"SYST:BEEP:STAT {'ON' if on else 'OFF'}"))
        fl.addRow("Unité température", self.cb_tunit)
        fl.addRow("Seuil continuité (Ω)", self.ed_cont)
        fl.addRow(self.ck_beep)
        fl.addRow(QtWidgets.QPushButton("Reset appareil (*RST)", clicked=lambda: self.cmd("*RST")))

        fl = self._group(lv, "Limites PASS / FAIL")
        self.ck_lim = self.R("lim_en", QtWidgets.QCheckBox("Activer"))
        self.ed_lo = self.R("lim_lo", QtWidgets.QLineEdit(placeholderText="ex. 4.65k"))
        self.ed_hi = self.R("lim_hi", QtWidgets.QLineEdit(placeholderText="ex. 4.75k"))
        self.ed_nom = self.R("nom", QtWidgets.QLineEdit(placeholderText="ex. 4.7k"))
        self.ed_tol = self.R("tol", QtWidgets.QLineEdit("1", maximumWidth=60))
        hb = QtWidgets.QHBoxLayout()
        for w in (self.ed_nom, QtWidgets.QLabel("±"), self.ed_tol, QtWidgets.QLabel("%"),
                  QtWidgets.QPushButton("→", clicked=self.nom_to_limits, maximumWidth=34)):
            hb.addWidget(w)
        self.ck_limbeep = self.R("lim_beep", QtWidgets.QCheckBox("Bip sur FAIL", checked=True))
        self.ck_lim.toggled.connect(self.update_limits)
        self.ed_lo.editingFinished.connect(self.update_limits)
        self.ed_hi.editingFinished.connect(self.update_limits)
        fl.addRow(self.ck_lim)
        fl.addRow("Mini", self.ed_lo)
        fl.addRow("Maxi", self.ed_hi)
        fl.addRow("Nominal", hb)
        fl.addRow(self.ck_limbeep)
        fl.addRow(QtWidgets.QPushButton("RAZ compteurs", clicked=self.reset_pf))

        fl = self._group(lv, "Capture auto (valeur stable)")
        self.ck_ah = self.R("ah_en", QtWidgets.QCheckBox("Activer"))
        self.sp_ahn = self.R("ah_n", QtWidgets.QSpinBox(minimum=2, maximum=100, value=5))
        self.sp_ahn.valueChanged.connect(lambda n: setattr(self, "ah_buf", deque(maxlen=n)))
        self.sp_ahtol = self.R("ah_tol", QtWidgets.QDoubleSpinBox(decimals=3, minimum=0.001, maximum=50, value=0.2, suffix=" %"))
        self.ed_ahmin = self.R("ah_min", QtWidgets.QLineEdit("0"))
        fl.addRow(self.ck_ah)
        fl.addRow("Échantillons stables", self.sp_ahn)
        fl.addRow("Tolérance", self.sp_ahtol)
        fl.addRow("|Valeur| mini", self.ed_ahmin)

        fl = self._group(lv, "Enregistrement CSV")
        self.bt_rec = QtWidgets.QPushButton("● Démarrer / armer", checkable=True, toggled=self.toggle_rec)
        self.cb_trig = self.R("trig", QtWidgets.QComboBox())
        self.cb_trig.addItems(["Immédiat", "Seuil montant", "Seuil descendant", "À une heure"])
        self.ed_trigthr = self.R("trig_thr", QtWidgets.QLineEdit("0"))
        self.te_trig = self.R("trig_time", QtWidgets.QTimeEdit(QtCore.QTime.currentTime().addSecs(600)))
        self.te_trig.setDisplayFormat("HH:mm:ss")
        self.sp_recdur = self.R("rec_dur", QtWidgets.QSpinBox(maximum=10_000_000, suffix=" s", specialValueText="illimitée"))
        self.sp_recdec = self.R("rec_dec", QtWidgets.QSpinBox(minimum=1, maximum=10000, value=1, prefix="1 mesure / "))
        self.ck_csvfr = QtWidgets.QCheckBox("Format Excel FR ( ; et virgule)", checked=True)
        self.lb_rec = QtWidgets.QLabel("—", wordWrap=True)
        fl.addRow("Déclencheur", self.cb_trig)
        fl.addRow("Seuil", self.ed_trigthr)
        fl.addRow("Heure", self.te_trig)
        fl.addRow("Durée", self.sp_recdur)
        fl.addRow("Décimation", self.sp_recdec)
        fl.addRow(self.ck_csvfr)
        fl.addRow(self.bt_rec)
        fl.addRow(self.lb_rec)

        fl = self._group(lv, "Intégration (charge / énergie)")
        self.lb_integ = QtWidgets.QLabel("—")
        self.lb_integ.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.ed_volt = self.R("integ_v", QtWidgets.QLineEdit("12", maximumWidth=80))
        fl.addRow(self.lb_integ)
        fl.addRow("Tension pour Wh (V)", self.ed_volt)
        fl.addRow(QtWidgets.QPushButton("RAZ intégrale", clicked=self.reset_integ))

        fl = self._group(lv, "Surveillance (anomalies)")
        self.ck_an = self.R("an_en", QtWidgets.QCheckBox("Activer"))
        self.ck_an_ol = self.R("an_ol", QtWidgets.QCheckBox("Dépassement / OL", checked=True))
        self.ck_an_spike = self.R("an_spike", QtWidgets.QCheckBox("Pics", checked=True))
        self.sp_an_k = self.R("an_k", QtWidgets.QDoubleSpinBox(minimum=2, maximum=100, value=6, suffix=" σ"))
        self.sp_an_n = self.R("an_n", QtWidgets.QSpinBox(minimum=5, maximum=1000, value=30, suffix=" pts"))
        self.ed_an_low = self.R("an_low", QtWidgets.QLineEdit(placeholderText="vide = désactivé"))
        self.ck_an_drift = self.R("an_drift", QtWidgets.QCheckBox("Dérive"))
        self.sp_an_dthr = self.R("an_dthr", QtWidgets.QDoubleSpinBox(decimals=3, minimum=0.001, maximum=100, value=0.5, suffix=" %/min"))
        self.sp_an_dwin = self.R("an_dwin", QtWidgets.QSpinBox(minimum=10, maximum=3600, value=60, suffix=" s"))
        self.ck_an_beep = self.R("an_beep", QtWidgets.QCheckBox("Bip / annonce", checked=True))
        hb = QtWidgets.QHBoxLayout()
        hb.addWidget(self.ck_an_spike)
        hb.addWidget(self.sp_an_k)
        hb.addWidget(self.sp_an_n)
        hb2 = QtWidgets.QHBoxLayout()
        hb2.addWidget(self.ck_an_drift)
        hb2.addWidget(self.sp_an_dthr)
        hb2.addWidget(self.sp_an_dwin)
        fl.addRow(self.ck_an)
        fl.addRow(self.ck_an_ol)
        fl.addRow(hb)
        fl.addRow("Décrochage si |v| <", self.ed_an_low)
        fl.addRow(hb2)
        fl.addRow(self.ck_an_beep)

        fl = self._group(lv, "Annonce vocale")
        self.cb_tts = self.R("tts", QtWidgets.QComboBox())
        self.cb_tts.addItems(["Désactivée", "Périodique", "Sur capture", "Sur FAIL / anomalie", "Capture + FAIL"])
        self.sp_tts = self.R("tts_int", QtWidgets.QSpinBox(minimum=2, maximum=3600, value=10, suffix=" s"))
        fl.addRow("Mode", self.cb_tts)
        fl.addRow("Période", self.sp_tts)
        fl.addRow(QtWidgets.QPushButton("Tester", clicked=lambda: self.speaker.say(
            speak_si(self.last_v, self.unit(), 4, self.noprefix()) if self.last_v is not None else "Synthèse vocale active")))

        fl = self._group(lv, "Services réseau")
        self.ck_web = QtWidgets.QCheckBox("Serveur web local")
        self.sp_webport = QtWidgets.QSpinBox(minimum=1024, maximum=65535, value=8088)
        self.ck_web.toggled.connect(self.toggle_web)
        self.lb_web = QtWidgets.QLabel("", openExternalLinks=True, textInteractionFlags=Qt.TextBrowserInteraction)
        self.ck_mqtt = QtWidgets.QCheckBox("Publication MQTT")
        self.ck_mqtt.toggled.connect(self.toggle_mqtt)
        self.lb_mqtt = QtWidgets.QLabel("", wordWrap=True)
        hb = QtWidgets.QHBoxLayout()
        hb.addWidget(self.ck_web)
        hb.addWidget(QtWidgets.QLabel("port"))
        hb.addWidget(self.sp_webport)
        hb2 = QtWidgets.QHBoxLayout()
        hb2.addWidget(self.ck_mqtt)
        hb2.addWidget(QtWidgets.QPushButton("Configurer…", clicked=self.mqtt_dialog))
        fl.addRow(hb)
        fl.addRow(self.lb_web)
        fl.addRow(hb2)
        fl.addRow(self.lb_mqtt)
        lv.addStretch()

    def _build_display(self, rv):
        disp = QtWidgets.QFrame(objectName="disp")
        disp.setStyleSheet("#disp{background:#05080a;border:2px solid #1e3a2a;border-radius:10px;}")
        dl = QtWidgets.QGridLayout(disp)
        self.lb_mode = QtWidgets.QLabel("")
        self.lb_flags = QtWidgets.QLabel("")
        self.lb_main = QtWidgets.QLabel("-----", alignment=Qt.AlignRight | Qt.AlignVCenter)
        self.lb_main.setFont(QtGui.QFont("Consolas", 64, QtGui.QFont.Bold))
        self.lb_main.setStyleSheet("color:#4cff7a;")
        self.lb_sec = QtWidgets.QLabel("", alignment=Qt.AlignRight)
        self.lb_sec.setFont(QtGui.QFont("Consolas", 22))
        self.lb_sec.setStyleSheet("color:#7ad7ff;")
        self.lb_pf = QtWidgets.QLabel("", alignment=Qt.AlignCenter, minimumWidth=110)
        self.lb_pf.setFont(QtGui.QFont("Segoe UI", 20, QtGui.QFont.Bold))
        for w in (self.lb_mode, self.lb_flags):
            w.setFont(QtGui.QFont("Segoe UI", 12))
            w.setStyleSheet("color:#c8c8c8;")
        dl.addWidget(self.lb_mode, 0, 0)
        dl.addWidget(self.lb_flags, 0, 1, alignment=Qt.AlignRight)
        dl.addWidget(self.lb_main, 1, 0, 1, 2)
        dl.addWidget(self.lb_pf, 2, 0, alignment=Qt.AlignLeft)
        dl.addWidget(self.lb_sec, 2, 1)
        rv.addWidget(disp)
        hb = QtWidgets.QHBoxLayout()
        self.bt_hold = QtWidgets.QPushButton("HOLD (H)", checkable=True, toggled=lambda on: setattr(self, "hold", on))
        self.bt_rel = QtWidgets.QPushButton("REL Δ (R)", checkable=True, toggled=self.toggle_rel)
        self.bt_snap = QtWidgets.QPushButton("📌 Capturer (Espace)", clicked=lambda: self.capture(self.last_v))
        self.bt_copy = QtWidgets.QPushButton("Copier (C)", clicked=self.copy_value)
        self.bt_clear = QtWidgets.QPushButton("Effacer données / stats", clicked=self.clear_data)
        for b in (self.bt_hold, self.bt_rel, self.bt_snap, self.bt_copy, self.bt_clear):
            hb.addWidget(b)
        rv.addLayout(hb)
        self.stat_box = QtWidgets.QGroupBox("Statistiques locales")
        sl = QtWidgets.QGridLayout(self.stat_box)
        self.st = {}
        for i, k in enumerate(["N", "Min", "Max", "Moyenne", "σ", "Crête-crête", "σ / moy", "PASS / FAIL"]):
            lab = QtWidgets.QLabel("—")
            lab.setFont(QtGui.QFont("Consolas", 11))
            lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
            sl.addWidget(QtWidgets.QLabel(k + " :"), i // 4, (i % 4) * 2)
            sl.addWidget(lab, i // 4, (i % 4) * 2 + 1)
            self.st[k] = lab
        rv.addWidget(self.stat_box)

    def _tab_chart(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        hb = QtWidgets.QHBoxLayout()
        self.cb_win = self.R("chart_win", QtWidgets.QComboBox())
        self.cb_win.addItems(["30 s", "1 min", "5 min", "15 min", "1 h", "Tout"])
        self.cb_win.setCurrentIndex(1)
        self.ck_chartpause = QtWidgets.QCheckBox("Figer")
        self.ck_mean = QtWidgets.QCheckBox("Ligne moyenne")
        self.ck_region = QtWidgets.QCheckBox("Région d'analyse (curseurs)")
        self.ck_region.toggled.connect(self.toggle_region)
        for x in (QtWidgets.QLabel("Fenêtre :"), self.cb_win, self.ck_chartpause, self.ck_mean, self.ck_region,
                  QtWidgets.QPushButton("Auto-échelle (A)", clicked=self.autoscale)):
            hb.addWidget(x)
        hb.addStretch()
        v.addLayout(hb)
        self.plot = self._plot(axisItems={"bottom": pg.DateAxisItem()})
        self.plot.setDownsampling(auto=True, mode="peak")
        self.plot.setClipToView(True)
        self.curve = self.plot.plot(pen=pg.mkPen(self.colors["curve"], width=1.5))
        self.ln_lo = pg.InfiniteLine(angle=0, pen=pg.mkPen("#ff5050", style=Qt.DashLine))
        self.ln_hi = pg.InfiniteLine(angle=0, pen=pg.mkPen("#ff5050", style=Qt.DashLine))
        self.ln_mean = pg.InfiniteLine(angle=0, pen=pg.mkPen("#ffb000", style=Qt.DotLine))
        for ln in (self.ln_lo, self.ln_hi, self.ln_mean):
            self.plot.addItem(ln)
            ln.hide()
        self.region = pg.LinearRegionItem(brush=pg.mkBrush(80, 160, 255, 40))
        self.region.sigRegionChanged.connect(self.update_region)
        self.plot.addItem(self.region)
        self.region.hide()
        v.addWidget(self.plot)
        self.lb_region = QtWidgets.QLabel("")
        self.lb_region.setFont(QtGui.QFont("Consolas", 10))
        self.lb_region.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.lb_region)
        self.tabs.addTab(w, "Graphique")

    def _tab_hist(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        hb = QtWidgets.QHBoxLayout()
        self.sp_bins = self.R("hist_bins", QtWidgets.QSpinBox(minimum=5, maximum=500, value=50))
        hb.addWidget(QtWidgets.QLabel("Classes :"))
        hb.addWidget(self.sp_bins)
        hb.addStretch()
        v.addLayout(hb)
        self.hplot = self._plot()
        v.addWidget(self.hplot)
        self.tabs.addTab(w, "Histogramme")

    def _tab_captures(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        hb = QtWidgets.QHBoxLayout()
        self.ck_bin = self.R("bin_en", QtWidgets.QCheckBox("Tri en bacs (série E)"))
        self.cb_bin = self.R("bin_series", QtWidgets.QComboBox())
        self.cb_bin.addItems(list(SERIES))
        self.cb_bin.setCurrentText("E24")
        self.sp_bintol = self.R("bin_tol", QtWidgets.QDoubleSpinBox(decimals=2, minimum=0.01, maximum=50, value=5, suffix=" %"))
        for x in (self.ck_bin, QtWidgets.QLabel("Série"), self.cb_bin, QtWidgets.QLabel("Tolérance"), self.sp_bintol):
            hb.addWidget(x)
        hb.addStretch()
        hb.addWidget(QtWidgets.QPushButton("Exporter CSV", clicked=self.export_captures))
        hb.addWidget(QtWidgets.QPushButton("Vider", clicked=self.clear_captures))
        v.addLayout(hb)
        sp = QtWidgets.QSplitter()
        self.tbl = QtWidgets.QTableWidget(0, 7)
        self.tbl.setHorizontalHeaderLabels(["#", "Heure", "Valeur", "Brut", "Statut", "Bac", "Écart %"])
        self.tbl.horizontalHeader().setStretchLastSection(True)
        self.tbl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl_bins = QtWidgets.QTableWidget(0, 2)
        self.tbl_bins.setHorizontalHeaderLabels(["Bac", "Nombre"])
        self.tbl_bins.horizontalHeader().setStretchLastSection(True)
        self.tbl_bins.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        sp.addWidget(self.tbl)
        sp.addWidget(self.tbl_bins)
        sp.setSizes([700, 250])
        v.addWidget(sp)
        self.tabs.addTab(w, "Captures / tri")

    def _tab_events(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        self.tbl_ev = QtWidgets.QTableWidget(0, 4)
        self.tbl_ev.setHorizontalHeaderLabels(["Heure", "Type", "Valeur", "Détail"])
        self.tbl_ev.horizontalHeader().setStretchLastSection(True)
        self.tbl_ev.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        hb = QtWidgets.QHBoxLayout()
        hb.addWidget(QtWidgets.QPushButton("Exporter CSV", clicked=lambda: self._export_table(self.tbl_ev, "evenements.csv")))
        hb.addWidget(QtWidgets.QPushButton("Vider", clicked=lambda: (self.tbl_ev.setRowCount(0), self.tabs.setTabText(3, "Événements"))))
        hb.addStretch()
        v.addWidget(self.tbl_ev)
        v.addLayout(hb)
        self.tabs.addTab(w, "Événements")

    def _tab_battery(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QHBoxLayout(w)
        left = QtWidgets.QWidget(maximumWidth=340)
        fl = QtWidgets.QFormLayout(left)
        self.cb_batload = self.R("bat_load", QtWidgets.QComboBox())
        self.cb_batload.addItems(["Courant constant (A)", "Charge résistive (Ω)"])
        self.ed_batval = self.R("bat_val", QtWidgets.QLineEdit("1"))
        self.ed_batcut = self.R("bat_cut", QtWidgets.QLineEdit("10.5"))
        self.bt_bat = QtWidgets.QPushButton("▶ Démarrer la décharge", checkable=True, toggled=self.toggle_battery)
        self.lb_bat = QtWidgets.QLabel("Mesure en V DC aux bornes de la batterie.", wordWrap=True)
        self.lb_bat.setFont(QtGui.QFont("Consolas", 10))
        self.lb_bat.setTextInteractionFlags(Qt.TextSelectableByMouse)
        fl.addRow("Charge", self.cb_batload)
        fl.addRow("Valeur", self.ed_batval)
        fl.addRow("Coupure (V)", self.ed_batcut)
        fl.addRow(self.bt_bat)
        fl.addRow(QtWidgets.QPushButton("Mémoriser V à vide", clicked=self.bat_v0))
        fl.addRow(QtWidgets.QPushButton("Calculer R interne (sous charge)", clicked=self.bat_rint))
        fl.addRow(self.lb_bat)
        v.addWidget(left)
        self.bplot = self._plot()
        self.bplot.setLabel("bottom", "Capacité", units="Ah")
        self.bplot.setLabel("left", "Tension", units="V")
        self.bcurve = self.bplot.plot(pen=pg.mkPen("#ffb74c", width=2))
        v.addWidget(self.bplot, 1)
        self.tabs.addTab(w, "Batterie")

    def _tab_conso(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QHBoxLayout(w)
        left = QtWidgets.QWidget(maximumWidth=360)
        fl = QtWidgets.QFormLayout(left)
        self.ed_cthr = self.R("cons_thr", QtWidgets.QLineEdit("1m"))
        self.ed_ccap = self.R("cons_cap", QtWidgets.QLineEdit("2000"))
        self.ck_clive = self.R("cons_live", QtWidgets.QCheckBox("Analyse continue"))
        hb = QtWidgets.QHBoxLayout()
        hb.addWidget(self.ed_cthr)
        hb.addWidget(QtWidgets.QPushButton("Auto", clicked=self.cons_auto))
        fl.addRow("Seuil veille/réveil (A)", hb)
        fl.addRow("Capacité batterie (mAh)", self.ed_ccap)
        fl.addRow(self.ck_clive)
        fl.addRow(QtWidgets.QPushButton("Analyser la fenêtre du graphique", clicked=self.cons_analyse))
        self.lb_cons = QtWidgets.QLabel("Mesure en A DC, sur la fenêtre affichée dans l'onglet Graphique.", wordWrap=True)
        self.lb_cons.setFont(QtGui.QFont("Consolas", 10))
        self.lb_cons.setTextInteractionFlags(Qt.TextSelectableByMouse)
        fl.addRow(self.lb_cons)
        v.addWidget(left)
        self.cplot_c = self._plot(axisItems={"bottom": pg.DateAxisItem()})
        self.cplot_c.setLogMode(y=False)
        self.ccurve = self.cplot_c.plot(pen=pg.mkPen("#7ad7ff", width=1.2))
        self.cthr_line = pg.InfiniteLine(angle=0, pen=pg.mkPen("#ffb000", style=Qt.DashLine))
        self.cplot_c.addItem(self.cthr_line)
        v.addWidget(self.cplot_c, 1)
        self.tabs.addTab(w, "Consommation")

    def _tab_compare(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QHBoxLayout(w)
        left = QtWidgets.QWidget(maximumWidth=300)
        lv = QtWidgets.QVBoxLayout(left)
        self.lw_traces = QtWidgets.QListWidget()
        self.lw_traces.itemChanged.connect(lambda _: self.draw_compare())
        lv.addWidget(QtWidgets.QPushButton("Charger CSV…", clicked=self.load_traces))
        lv.addWidget(QtWidgets.QPushButton("Ajouter la session courante", clicked=self.add_current_trace))
        lv.addWidget(self.lw_traces)
        lv.addWidget(QtWidgets.QPushButton("Supprimer la sélection", clicked=self.remove_trace))
        v.addWidget(left)
        self.cmpplot = self._plot()
        self.cmpplot.setLabel("bottom", "Temps", units="s")
        self.cmpplot.addLegend()
        v.addWidget(self.cmpplot, 1)
        self.tabs.addTab(w, "Comparaison")

    def _tab_seq(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        hb = QtWidgets.QHBoxLayout()
        self.bt_seqrun = QtWidgets.QPushButton("▶ Exécuter", clicked=self.seq_run)
        self.bt_seqstop = QtWidgets.QPushButton("■ Arrêter", clicked=lambda: self.seq.stop())
        self.lb_verdict = QtWidgets.QLabel("")
        self.lb_verdict.setFont(QtGui.QFont("Segoe UI", 14, QtGui.QFont.Bold))
        for x in (self.bt_seqrun, self.bt_seqstop, QtWidgets.QPushButton("Ouvrir…", clicked=self.seq_load),
                  QtWidgets.QPushButton("Enregistrer…", clicked=self.seq_save),
                  QtWidgets.QPushButton("Exemple", clicked=lambda: self.ed_seq.setPlainText(EXAMPLE)),
                  QtWidgets.QPushButton("Aide", clicked=lambda: QtWidgets.QMessageBox.information(self, "Séquences", HELP))):
            hb.addWidget(x)
        hb.addStretch()
        hb.addWidget(self.lb_verdict)
        v.addLayout(hb)
        sp = QtWidgets.QSplitter()
        self.ed_seq = QtWidgets.QPlainTextEdit()
        self.ed_seq.setFont(QtGui.QFont("Consolas", 10))
        self.ed_seq.setPlainText(self.cfg.value("seq_text", EXAMPLE))
        self.seq_out = QtWidgets.QPlainTextEdit(readOnly=True)
        self.seq_out.setFont(QtGui.QFont("Consolas", 10))
        sp.addWidget(self.ed_seq)
        sp.addWidget(self.seq_out)
        v.addWidget(sp)
        self.tabs.addTab(w, "Séquences")

    def _tab_console(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        self.con_out = QtWidgets.QPlainTextEdit(readOnly=True)
        self.con_out.setFont(QtGui.QFont("Consolas", 10))
        self.con_in = QtWidgets.QLineEdit(placeholderText="Commande SCPI (ex. FUNC?, RANGE?, CALC:AVER:ALL?) + Entrée")
        self.con_in.returnPressed.connect(self.console_send)
        self.con_hist, self.con_hi = [], 0
        self.con_in.installEventFilter(self)
        v.addWidget(self.con_out)
        v.addWidget(self.con_in)
        self.tabs.addTab(w, "Console SCPI")

    def _shortcuts(self):
        for key, fn in (("H", lambda: self.bt_hold.toggle()), ("R", lambda: self.bt_rel.toggle()),
                        ("Space", lambda: self.capture(self.last_v)), ("P", lambda: self.bt_run.toggle()),
                        ("C", self.copy_value), ("A", self.autoscale)):
            sc = QtGui.QShortcut(QtGui.QKeySequence(key), self)
            sc.activated.connect(fn)

    # ================================================================== réglages / profils
    def _load_settings(self):
        c = self.cfg
        b = lambda k, d: str(c.value(k, d)).lower() == "true"
        self.act_autorec.setChecked(b("autorec", True))
        self.act_fast.setChecked(b("fast", True))
        self.act_restore.setChecked(b("restore", True))
        self.act_rangecmd.setChecked(b("rangecmd", True))
        self.ck_csvfr.setChecked(b("csvfr", True))
        self.ck_beep.blockSignals(True)
        self.ck_beep.setChecked(b("beep", True))
        self.ck_beep.blockSignals(False)
        self.sp_webport.setValue(int(c.value("web_port", 8088)))
        self.act_light.setChecked(self.theme == "light")
        self.set_zoom(int(c.value("zoom", 100)), save=False)
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
        for k, a in (("autorec", self.act_autorec), ("fast", self.act_fast), ("restore", self.act_restore), ("rangecmd", self.act_rangecmd)):
            c.setValue(k, "true" if a.isChecked() else "false")
        for k, w in (("csvfr", self.ck_csvfr), ("beep", self.ck_beep), ("web", self.ck_web), ("mqtt", self.ck_mqtt)):
            c.setValue(k, "true" if w.isChecked() else "false")
        c.setValue("web_port", self.sp_webport.value())
        c.setValue("theme", self.theme)
        c.setValue("geom", self.saveGeometry())
        c.setValue("seq_text", self.ed_seq.toPlainText())
        c.setValue("last_state", json.dumps(self.get_state()))

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
        self.filt = deque(maxlen=self.sp_avg.value())
        self.ah_buf = deque(maxlen=self.sp_ahn.value())
        if self.worker:
            self.worker.interval = self.sp_int.value() / 1000
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
        self.lb_idn.setText(idn)
        self.lb_idn.setStyleSheet("")
        self.bt_conn.setText("Déconnecter")
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
        self.lb_idn.setText("⚠ " + msg)
        self.lb_idn.setStyleSheet("color:#ffb000;")
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
        self.lb_idn.setText("Déconnecté")
        self.lb_idn.setStyleSheet("")
        self.lb_rate.setText("")
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
            self.tabs.setCurrentIndex(self.tabs.count() - 1)
        elif tag == "console":
            self.log(f"<< {r}")
        elif tag == "seq":
            self.seq.on_reply(r)

    # ================================================================== commandes DMM
    def set_mode(self, key, send=True, clear=True):
        m = MODES[key]
        changed = key != self.mode
        self.mode = key
        self.unit_override = None
        for b in self.fbtn.buttons():
            b.setChecked(b.property("key") == key)
        if changed or self.cb_range.count() != len(m["ranges"]):
            self.cb_range.blockSignals(True)
            self.cb_range.clear()
            for lab, _ in m["ranges"]:
                self.cb_range.addItem(lab)
            self.cb_range.blockSignals(False)
        self.cb_range.setEnabled(bool(m["ranges"]))
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
                self.cb_range.setCurrentIndex(i)
                self.set_range(i)
                return True
        x = try_si(label)
        if x is not None:
            for i, (_, val) in enumerate(m["ranges"]):
                vv = try_si(val)
                if vv and abs(vv - x) / vv < 1e-6:
                    self.cb_range.setCurrentIndex(i)
                    self.set_range(i)
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

    def update_mode_label(self):
        rng = self.cb_range.currentText() if self.cb_range.count() else ""
        self.lb_mode.setText(f"{MODES[self.mode]['label']}   {rng}   {self.cb_speed.currentText()}")
        u = self.unit()
        for p in (self.plot, self.hplot):
            ax = "left" if p is self.plot else "bottom"
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
            self.lb_sec.setText("")

    def set_tunit(self, _):
        self.cmd(f"TEMP:RTD:UNIT {self.cb_tunit.currentText()}")
        self.update_mode_label()

    def toggle_run(self, paused):
        self.bt_run.setText("▶ Reprendre acquisition" if paused else "⏸ Pause acquisition")
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
        QtWidgets.QApplication.clipboard().setText(self.lb_main.text())

    def toggle_rel(self, on):
        self.rel = self.last_v if on and self.last_v is not None and math.isfinite(self.last_v) else None
        if on and self.rel is None:
            self.bt_rel.blockSignals(True)
            self.bt_rel.setChecked(False)
            self.bt_rel.blockSignals(False)
        self.bt_rel.setText(f"REL Δ ({self.fmt(self.rel, 5)})" if self.rel is not None else "REL Δ (R)")
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
        if not on:
            self.lb_pf.setText("")
            self.lb_pf.setStyleSheet("")

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
        for c, t in enumerate(items):
            it = QtWidgets.QTableWidgetItem(t)
            if st or bac.startswith("Hors"):
                bad = st == "FAIL" or bac.startswith("Hors")
                it.setForeground(QtGui.QColor("#ff5050" if bad else "#2fbf5f"))
            self.tbl.setItem(r, c, it)
        self.tbl.scrollToBottom()
        bad = st == "FAIL" or bac.startswith("Hors")
        self.beep("fail" if bad else "ok")
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

        for k in sorted(self.bins, key=lambda k: key(k)):
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
        self.tabs.setTabText(3, f"Événements ({r + 1})")

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
            self.lb_pf.setText(pf)
            self.lb_pf.setStyleSheet(f"color:#05080a;background:{'#4cff7a' if pf == 'PASS' else '#ff5050'};border-radius:6px;padding:2px 10px;")
        else:
            self.pf = None
        self.autohold(v)
        self.rec_process(t, v, raw, s, pf)
        self.anomaly(t, v)
        if self.battery.running and self.battery.feed(t, raw):
            self.battery_done()
        self.seq.on_reading(v)
        color = "#4cff7a"
        if not self.hold:
            self.filt.append(v)
            fin = [x for x in self.filt if math.isfinite(x)]
            dv = (sum(fin) / len(fin)) if fin and len(fin) == len(self.filt) else v
            self.disp_text = self.fmt(dv)
            self.lb_main.setText(self.disp_text)
            if s is not None:
                self.lb_sec.setText(fmt_si(s, "Hz", 6))
            if self.mode == "CONT" and math.isfinite(v):
                if v <= try_si(self.ed_cont.text(), 50):
                    color = "#ffd84c"
            self.lb_main.setStyleSheet(f"color:{color};")
        sec_txt = fmt_si(s, "Hz", 6) if s is not None else ""
        if self.floating:
            self.floating.update_view(self.disp_text, self.lb_mode.text(), sec_txt, pf, color)
        if self.ck_web.isChecked():
            S = self.stats
            st = f"N {S.n}  min {self.fmt(S.min, 5)}  max {self.fmt(S.max, 5)}  moy {self.fmt(S.mean, 5)}" if S.n else ""
            self.web.update(t, v, display=self.disp_text, unit=self.unit(), mode=self.lb_mode.text(), sec=sec_txt,
                            pf=pf or "", stats=st, online=True)
        if self.ck_mqtt.isChecked():
            self.mqtt.publish(t, v, self.unit(), MODES[self.mode]["label"], pf, s, self.disp_text)
        flags = [f for f, on in (("HOLD", self.hold), ("REL", self.rel is not None), ("MATH", self.cb_math.currentIndex()),
                                 ("● REC", self.rec_state == "rec"), ("◌ ARMÉ", self.rec_state == "armed"),
                                 (f"AVG{self.sp_avg.value()}", self.sp_avg.value() > 1), ("BATT", self.battery.running),
                                 ("SEQ", self.seq.running), ("WEB", self.ck_web.isChecked()), ("MQTT", self.ck_mqtt.isChecked())) if on]
        self.lb_flags.setText("  ".join(flags))

    # ================================================================== enregistrement
    def toggle_rec(self, on):
        if on:
            fn, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Enregistrer sous", f"XDM1041_{datetime.datetime.now():%Y%m%d_%H%M%S}.csv", "CSV (*.csv)")
            if not fn:
                self.bt_rec.blockSignals(True)
                self.bt_rec.setChecked(False)
                self.bt_rec.blockSignals(False)
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
            self.bt_rec.setText("■ Arrêter")
        else:
            if self.rec_file:
                self.rec_file.close()
            self.rec_file = self.rec_writer = None
            self.rec_state = None
            self.bt_rec.setText("● Démarrer / armer")
            self.lb_rec.setText(f"Terminé — {self.rec_count} lignes")

    def _rec_start(self, t):
        self.rec_state = "rec"
        self.rec_t0 = t
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
            self.bt_bat.setText("■ Arrêter la décharge")
            self.add_event("Batterie", self.last_v, "début de décharge")
        else:
            self.battery.running = False
            self.bt_bat.setText("▶ Démarrer la décharge")

    def battery_done(self):
        self.bt_bat.blockSignals(True)
        self.bt_bat.setChecked(False)
        self.bt_bat.blockSignals(False)
        self.bt_bat.setText("▶ Démarrer la décharge")
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
            h = cap / 1000 / r["avg"]
            aut = f"\nAutonomie estimée : {h:,.1f} h  ({h / 24:,.1f} j)".replace(",", " ")
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
            self.region.setRegion((t0 + (t1 - t0) * 0.6, t1 - (t1 - t0) * 0.1))
        self.region.setVisible(on)
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
        if self.worker:
            self.lb_rate.setText(f"{len(self.rate_ts) / 2:.1f} mes/s")
        u, npf, dg = self.unit(), self.noprefix(), self.sp_dig.value()
        f = lambda x: fmt_si(x, u, dg, npf)
        S = self.stats
        if S.n:
            for k, val in (("N", str(S.n)), ("Min", f(S.min)), ("Max", f(S.max)), ("Moyenne", f(S.mean)), ("σ", f(S.std)),
                           ("Crête-crête", f(S.max - S.min)), ("σ / moy", f"{100 * S.std / abs(S.mean):.4f} %" if S.mean else "—")):
                self.st[k].setText(val)
        else:
            for k in self.st:
                self.st[k].setText("—")
        self.st["PASS / FAIL"].setText(f"{self.npass} / {self.nfail}")
        h = self.integ / 3600
        if self.mode in ("IDC", "IAC"):
            volt = try_si(self.ed_volt.text(), 0.0)
            self.lb_integ.setText(f"Q = {fmt_si(h, 'Ah', 6)}   ({fmt_si(self.integ, 'C', 6)})\nE ≈ {fmt_si(h * volt, 'Wh', 6)} @ {volt:g} V")
        else:
            self.lb_integ.setText(f"∫ = {fmt_si(self.integ, u + '·s', 6, npf)}  /  {fmt_si(h, u + '·h', 6, npf)}")
        # déclencheur horaire / durée
        if self.rec_state == "armed" and self.cb_trig.currentIndex() == 3 and self.rec_target and now >= self.rec_target:
            self._rec_start(now)
        if self.rec_state == "rec" and self.sp_recdur.value() and now - self.rec_t0 >= self.sp_recdur.value():
            self.bt_rec.setChecked(False)
        # annonce périodique
        if self.cb_tts.currentIndex() == 1 and self.worker and now - self.tts_last >= self.sp_tts.value() and self.last_v is not None:
            self.tts_last = now
            self.speaker.say(speak_si(self.last_v - (self.rel or 0), u, 4, npf))
        # batterie
        B = self.battery
        if B.pts and (B.running or self.tabs.currentIndex() == 4):
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
        self.curve.setData(t, yp, connect="finite")
        if self.ck_mean.isChecked() and S.n:
            self.ln_mean.setValue(S.mean)
            self.ln_mean.show()
        else:
            self.ln_mean.hide()
        tab = self.tabs.currentIndex()
        if tab == 1:
            yf = y[np.isfinite(y)]
            self.hplot.clear()
            if yf.size > 1 and yf.max() > yf.min():
                cnt, edges = np.histogram(yf, bins=self.sp_bins.value())
                self.hplot.addItem(pg.BarGraphItem(x0=edges[:-1], x1=edges[1:], height=cnt, brush="#4cff7a88", pen=self.colors["curve"]))
        elif tab == 5:
            self.ccurve.setData(t, yp, connect="finite")
            if self.ck_clive.isChecked() and int(now * 2) % 4 == 0:
                self.cons_analyse()

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
        self.tabs.setCurrentIndex(6)
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
        col = {"PASS": "#2fbf5f", "FAIL": "#ff5050"}.get(verdict, "#ffb000")
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
            self.web.set(online=bool(self.worker), unit=self.unit(), mode=self.lb_mode.text())
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
            self.tabs.setCurrentIndex(1)
            self.refresh_views()
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

    def toggle_compact(self):
        c = self.act_compact.isChecked()
        for w in (self.left, self.tabs, self.stat_box):
            w.setVisible(not c)
        if c:
            self.resize(700, 280)

    def toggle_floating(self):
        if self.act_float.isChecked():
            self.floating = FloatingDisplay()
            self.floating.closed.connect(self._floating_closed)
            g = self.cfg.value("float_pos")
            if g:
                self.floating.move(g)
            self.floating.update_view(self.disp_text, self.lb_mode.text(), "", self.pf)
            self.floating.show()
        elif self.floating:
            self.floating.close()

    def _floating_closed(self):
        if self.floating:
            self.cfg.setValue("float_pos", self.floating.pos())
        self.floating = None
        self.act_float.setChecked(False)

    def toggle_theme(self):
        self.theme = "light" if self.act_light.isChecked() else "dark"
        self.colors = apply_theme(QtWidgets.QApplication.instance(), self.theme)
        for p in self.plots:
            p.setBackground(self.colors["plot"])
            for ax in ("left", "bottom"):
                p.getAxis(ax).setPen(self.colors["fg"])
                p.getAxis(ax).setTextPen(self.colors["fg"])
        self.curve.setPen(pg.mkPen(self.colors["curve"], width=1.5))
        self.cfg.setValue("theme", self.theme)

    def set_zoom(self, pct, save=True):
        app = QtWidgets.QApplication.instance()
        f = app.font()
        f.setPointSizeF(9 * pct / 100)
        app.setFont(f)
        for a in self.zoom_group.actions():
            a.setChecked(a.data() == pct)
        if save:
            self.cfg.setValue("zoom", pct)

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
    w = Main()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
