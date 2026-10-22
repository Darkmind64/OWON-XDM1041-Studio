# -*- coding: utf-8 -*-
"""Thèmes : préréglages de couleurs, feuille de style Qt, icônes vectorielles, polices embarquées."""
import os, sys, glob, json
from string import Template
from PySide6 import QtCore, QtGui, QtSvg

UI_FONT = "Inter"
FONTS = ["JetBrains Mono", "Share Tech Mono", "Roboto Mono", "Consolas"]

PRESETS = {
    "Phosphore": dict(bg="#0e1014", surface="#161a20", surface2="#1a1e25", border="#262c35", text="#e6e9ee", muted="#8b94a3",
                      accent="#3ddc84", disp_bg="#07090b", disp_border="#1d3a2a", digit="#3ddc84", sec="#5aa9ff", annun_off="#1f2a23",
                      plot="#0f1216", grid="#1f252d", curve="#3ddc84", pass_="#3ddc84", fail="#ff5d5d", warn="#ffd84c",
                      btn="#1a1e25", btn_text="#e6e9ee", btn_on="#13241a"),
    "Ambre": dict(bg="#141517", surface="#222427", surface2="#2b2e32", border="#3a3d42", text="#e8e6e1", muted="#9a968e",
                  accent="#ffb547", disp_bg="#0b0c0b", disp_border="#050505", digit="#ffb547", sec="#ffd9a0", annun_off="#2a2924",
                  plot="#0b0c0b", grid="#1d1f1c", curve="#ffb547", pass_="#9ee37d", fail="#ff6a3d", warn="#ffe066",
                  btn="#2b2e32", btn_text="#e8e6e1", btn_on="#3a3226"),
    "Glacier": dict(bg="#0b1118", surface="#121a23", surface2="#17212c", border="#233244", text="#e3eef8", muted="#8ea3b8",
                    accent="#8fd3ff", disp_bg="#071019", disp_border="#1b3550", digit="#8fd3ff", sec="#c8b6ff", annun_off="#16222f",
                    plot="#0a131c", grid="#17222e", curve="#8fd3ff", pass_="#5ee0b4", fail="#ff7a90", warn="#ffe08a",
                    btn="#17212c", btn_text="#e3eef8", btn_on="#11283b"),
    "Nixie": dict(bg="#120a08", surface="#1c1210", surface2="#241714", border="#3a231c", text="#f6e1d8", muted="#b39488",
                  accent="#ff7a3d", disp_bg="#0d0605", disp_border="#4a2116", digit="#ff7a3d", sec="#ffb48a", annun_off="#2a1612",
                  plot="#100807", grid="#2a1612", curve="#ff7a3d", pass_="#c6e377", fail="#ff3d5e", warn="#ffd166",
                  btn="#241714", btn_text="#f6e1d8", btn_on="#3a1a10"),
    "Clair": dict(bg="#f3f4f6", surface="#ffffff", surface2="#f9fafb", border="#d1d5db", text="#111827", muted="#4b5563",
                  accent="#2563eb", disp_bg="#ffffff", disp_border="#d1d5db", digit="#0f172a", sec="#2563eb", annun_off="#e5e7eb",
                  plot="#ffffff", grid="#eef0f3", curve="#2563eb", pass_="#059669", fail="#dc2626", warn="#d97706",
                  btn="#f9fafb", btn_text="#111827", btn_on="#eff6ff"),
}
OPTIONS = dict(digit_font="JetBrains Mono", digit_size=128, glow=40, pf_color=True, blink_ol=True, bargraph=False,
               curve_width=2.0, skin="atelier", nav="rail")

COLOR_GROUPS = {
    "Thème": [("disp_bg", "Fond de l’afficheur"), ("digit", "Chiffres"), ("sec", "Afficheur secondaire"), ("curve", "Courbe"),
              ("pass_", "PASS"), ("fail", "FAIL / limites"), ("plot", "Fond du graphique"), ("grid", "Grille"),
              ("btn", "Boutons"), ("border", "Bordures")],
    "Afficheur": [("disp_bg", "Fond"), ("disp_border", "Cadre"), ("digit", "Chiffres"), ("sec", "Secondaire"),
                  ("annun_off", "Voyants éteints")],
    "Graphique": [("plot", "Fond"), ("grid", "Grille"), ("curve", "Courbe"), ("fail", "Limites")],
    "États": [("pass_", "PASS"), ("fail", "FAIL"), ("warn", "Continuité / alerte")],
    "Interface": [("bg", "Fond de fenêtre"), ("surface", "Cartes"), ("surface2", "Cartes secondaires"), ("border", "Bordures"),
                  ("text", "Texte"), ("muted", "Texte secondaire"), ("accent", "Accent"), ("btn", "Boutons"),
                  ("btn_text", "Texte des boutons"), ("btn_on", "Bouton actif")],
}

CURRENT = {}

ICONS = {
    "mesure": "M3 14a7 7 0 1 1 14 0M10 14l3-4M4 17h12",
    "analyse": "M3 16l4-6 3 3 4-7 3 4M3 3v14h14",
    "tri": "M3 5h14M5 10h10M8 15h4",
    "batterie": "M3 6h12v8H3zM15 9h2v2h-2M6 9v2M9 9v2",
    "conso": "M11 2L4 11h5l-1 7 7-9h-5z",
    "sequences": "M4 4h12M4 8h8M4 12h12M4 16h6",
    "journal": "M5 3h10v14H5zM8 7h4M8 10h4M8 13h2",
    "reglages": "M10 7.5a2.5 2.5 0 1 0 0 5a2.5 2.5 0 1 0 0-5M10 2v2.5M10 15.5V18M2 10h2.5M15.5 10H18M4.3 4.3l1.8 1.8M13.9 13.9l1.8 1.8M4.3 15.7l1.8-1.8M13.9 6.1l1.8-1.8",
    "hold": "M7 4v12M13 4v12",
    "rel": "M10 3L17 16H3z",
    "capture": "M6 3h8l-2 6 3 3H5l3-3zM10 12v6",
    "copy": "M7 7h9v10H7zM4 13V3h9",
    "trash": "M4 6h12M8 6V4h4v2M6 6l1 11h6l1-11",
    "pause": "M7 4v12M13 4v12",
    "play": "M6 4l10 6-10 6z",
    "layout": "M2.5 2.5h6.5v6.5H2.5zM11 2.5h6.5v6.5H11zM2.5 11h15v6.5h-15z",
    "palette": "M10 2.5a7.5 7.5 0 1 0 0 15c1 0 1.5-.7 1.5-1.5s-.6-1.2-.6-2 .7-1.5 1.6-1.5H15a2.5 2.5 0 0 0 2.5-2.5C17.5 5.6 14.1 2.5 10 2.5zM6 9.5h.01M8.5 6h.01M12.5 6h.01",
    "menu": "M3 5h14M3 10h14M3 15h14",
    "refresh": "M16 10a6 6 0 1 1-1.8-4.3M16 3v4h-4",
    "plug": "M7 2v4M13 2v4M5 6h10v3a5 5 0 0 1-10 0zM10 14v4",
    "logo": "M3 12h3.5l2-6 3 10 2-6.5 1.2 2.5H17",
}


def preset(name):
    t = dict(OPTIONS)
    t.update(PRESETS[name])
    t["name"] = name
    return t


def complete(t):
    base = preset("Phosphore")
    base.update({k: v for k, v in (t or {}).items() if k in base or k == "name"})
    return base


def resource_path(*p):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, *p)


def load_fonts():
    fams = set()
    for f in glob.glob(resource_path("fonts", "*.ttf")) + glob.glob(resource_path("fonts", "*.otf")):
        fid = QtGui.QFontDatabase.addApplicationFont(f)
        if fid >= 0:
            fams.update(QtGui.QFontDatabase.applicationFontFamilies(fid))
    return fams


def available_digit_fonts():
    fams = set(QtGui.QFontDatabase.families())
    out = [f for f in FONTS if f in fams]
    return out or ["Consolas", "Courier New"]


def qc(c):
    return QtGui.QColor(c)


def mix(a, b, t):
    a, b = qc(a), qc(b)
    return QtGui.QColor(round(a.red() + (b.red() - a.red()) * t), round(a.green() + (b.green() - a.green()) * t),
                        round(a.blue() + (b.blue() - a.blue()) * t)).name()


def lum(c):
    c = qc(c)

    def ch(x):
        x /= 255
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c.red()) + 0.7152 * ch(c.green()) + 0.0722 * ch(c.blue())


def on_color(c):
    return "#0b0d10" if lum(c) > 0.32 else "#ffffff"


def is_light(t):
    return lum(t["bg"]) > 0.5


def svg_pixmap(path, color, size, stroke=1.7, dpr=2.0, fill=False):
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="{size}" height="{size}">'
           f'<path d="{path}" fill="{color if fill else "none"}" stroke="{color}" stroke-width="{stroke}" '
           f'stroke-linecap="round" stroke-linejoin="round"/></svg>')
    r = QtSvg.QSvgRenderer(QtCore.QByteArray(svg.encode()))
    pm = QtGui.QPixmap(int(size * dpr), int(size * dpr))
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    r.render(p)
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name, color, size=20, **kw):
    ic = QtGui.QIcon()
    ic.addPixmap(svg_pixmap(ICONS.get(name, name), color, size, **kw))
    return ic


def _asset(name, svg):
    d = os.path.join(QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.AppDataLocation), "theme")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(svg)
    return p.replace("\\", "/")


QSS = Template("""
QMainWindow, QDialog, QWidget#Root { background: $bg; }
QWidget { color: $text; }
QWidget#Page, QWidget#Transparent, QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }
QFrame#Header { background: $header; border-bottom: 1px solid $border; }
QFrame#TabsRow { background: $header; border-bottom: 1px solid $border; }
QFrame#Card { background: $surface; border: 1px solid $border; border-radius: 14px; }
QFrame#Card[edit="true"] { border: 1.5px dashed $muted; }
QFrame#Card[skin="instrument"] { background: $surface; border: 1px solid $border; border-radius: 18px; }
QFrame#StatCard { background: $surface; border: 1px solid $border; border-radius: 12px; }
QLabel#CardTitle { color: $muted; font-weight: 600; }
QLabel#Muted, QLabel#StatKey { color: $muted; }
QLabel#Title { font-weight: 700; }
QLabel#Pill { background: $btn; color: $muted; border: 1px solid $border; border-radius: 15px; padding: 5px 14px; }
QLabel#Pill[on="true"] { background: $pill_bg; color: $pill_fg; border-color: $pill_border; }
QLabel#Pill[lost="true"] { background: $warn_bg; color: $warn; border-color: $warn; }
QFrame#Display { background: $disp_bg; border: 1px solid $disp_border; border-radius: 16px; }
QFrame#Display[skin="instrument"] { border: 3px solid $disp_border; border-radius: 12px; }
QLabel#DispChip { background: $chip_bg; color: $chip_fg; border-radius: 6px; padding: 2px 8px; border: 1px solid transparent; }
QLabel#DispChip:hover { border: 1px solid $digit; }
QLabel#DispChip[accent="true"] { background: $btn_on; color: $digit; font-weight: 600; }
QLabel#DispChip[warn="true"] { background: $warn_bg; color: $warn; font-weight: 600; }
QLabel#PF { border-radius: 8px; padding: 4px 14px; font-weight: 700; }
QLabel#PF[pf="PASS"] { background: $pass_; color: $pass_text; }
QLabel#PF[pf="FAIL"] { background: $fail; color: $fail_text; }
QFrame#Banner { background: $banner_bg; border-bottom: 1px solid $banner_border; }
QLabel#BannerText { color: $banner_text; }
QFrame#SidePanel { background: $header; border-left: 1px solid $border; }
QFrame#SegBox { background: $input; border: 1px solid $border; border-radius: 10px; }

QPushButton { background: $btn; color: $btn_text; border: 1px solid $border; border-radius: 9px; padding: 6px 14px; min-height: 22px; }
QPushButton:hover { border-color: $hover_border; }
QPushButton:pressed { background: $btn_press; }
QPushButton:checked { background: $btn_on; color: $accent; border-color: $accent; }
QPushButton:disabled { color: $muted; background: $surface; border-color: $border; }
QPushButton#Primary { background: $accent; color: $accent_text; border: 1px solid $accent; font-weight: 600; }
QPushButton#Primary:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton#Primary:checked { background: $btn; color: $text; border-color: $border; }
QPushButton#Chip { border-radius: 13px; padding: 3px 11px; min-height: 18px; background: transparent; }
QPushButton#Chip:checked { background: $accent; color: $accent_text; border-color: $accent; font-weight: 600; }
QPushButton#Seg { border: none; border-radius: 7px; background: transparent; color: $muted; padding: 5px 12px; }
QPushButton#Seg:checked { background: $seg_on; color: $text; font-weight: 600; }
QPushButton#Seg:hover { color: $text; }
QPushButton#Ghost { background: transparent; border: 1px solid $banner_border; color: $banner_text; }
QPushButton#Info { background: $sec; color: $sec_text; border: none; font-weight: 700; }
QToolButton { background: $btn; color: $btn_text; border: 1px solid $border; border-radius: 9px; padding: 6px; }
QToolButton:hover { border-color: $hover_border; }
QToolButton:checked { background: $btn_on; border-color: $accent; }
QToolButton::menu-indicator { image: none; width: 0; }

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTimeEdit {
  background: $input; color: $text; border: 1px solid $border; border-radius: 8px; padding: 4px 9px; min-height: 22px;
  selection-background-color: $accent; selection-color: $accent_text; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QTimeEdit:focus { border-color: $accent; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox::down-arrow { image: url($arrow); width: 10px; height: 10px; }
QComboBox QAbstractItemView { background: $surface; color: $text; border: 1px solid $border; selection-background-color: $btn_on; selection-color: $text; outline: none; padding: 4px; }
QSpinBox::up-button, QDoubleSpinBox::up-button, QTimeEdit::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 18px; border: none; background: transparent; }
QSpinBox::down-button, QDoubleSpinBox::down-button, QTimeEdit::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 18px; border: none; background: transparent; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow, QTimeEdit::up-arrow { image: url($up); width: 8px; height: 8px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow, QTimeEdit::down-arrow { image: url($arrow); width: 8px; height: 8px; }
QPlainTextEdit, QTableWidget, QListWidget { background: $input; color: $text; border: 1px solid $border; border-radius: 10px;
  selection-background-color: $btn_on; selection-color: $text; gridline-color: $border; }
QHeaderView::section { background: $surface; color: $muted; border: none; border-bottom: 1px solid $border; padding: 6px 8px; font-weight: 600; }
QTableCornerButton::section { background: $surface; border: none; }
QCheckBox { spacing: 8px; background: transparent; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px; border: 1px solid $hover_border; background: $input; }
QCheckBox::indicator:checked { background: $accent; border-color: $accent; image: url($check); }
QGroupBox { background: $surface; border: 1px solid $border; border-radius: 14px; margin-top: 16px; padding: 18px 12px 12px 12px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 12px; padding: 0 6px; color: $muted; font-weight: 600; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: transparent; color: $muted; padding: 8px 14px; border: none; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: $text; border-bottom-color: $accent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: $scroll; border-radius: 4px; min-height: 30px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: $scroll; border-radius: 4px; min-width: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
QMenu { background: $surface; color: $text; border: 1px solid $border; border-radius: 10px; padding: 6px; }
QMenu::item { padding: 6px 22px 6px 14px; border-radius: 6px; }
QMenu::item:selected { background: $btn_on; color: $text; }
QMenu::separator { height: 1px; background: $border; margin: 6px 8px; }
QMenu::indicator { width: 14px; height: 14px; }
QSlider::groove:horizontal { height: 4px; background: $border; border-radius: 2px; }
QSlider::sub-page:horizontal { background: $accent; border-radius: 2px; }
QSlider::handle:horizontal { background: $accent; width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; }
QStatusBar { background: $header; color: $muted; border-top: 1px solid $border; }
QToolTip { background: $surface; color: $text; border: 1px solid $border; padding: 4px 8px; }
QMainWindow::separator { background: transparent; width: 10px; height: 10px; }
QMainWindow::separator:hover { background: $sep_hover; }
QDockWidget { color: $text; }
QSplitter::handle { background: transparent; }
""")


def build_qss(t):
    light = is_light(t)
    d = {k.rstrip("_") + ("_" if k.endswith("_") else ""): v for k, v in t.items() if isinstance(v, str)}
    d.update(
        header=mix(t["bg"], t["surface"], 0.55),
        input=mix(t["bg"], "#000000" if not light else "#ffffff", 0.25 if not light else 0.6),
        hover_border=mix(t["border"], t["text"], 0.28),
        btn_press=mix(t["btn"], t["text"], 0.08),
        accent_text=on_color(t["accent"]),
        accent_hover=mix(t["accent"], "#ffffff" if not light else "#000000", 0.15),
        seg_on=mix(t["surface"], t["text"], 0.12),
        chip_bg=mix(t["disp_bg"], t["text"], 0.08),
        chip_fg=mix(t["muted"], t["text"], 0.4),
        pass_text=on_color(t["pass_"]),
        fail_text=on_color(t["fail"]),
        pill_bg=mix(t["bg"], t["pass_"], 0.12),
        pill_fg=mix(t["pass_"], t["text"], 0.45),
        pill_border=mix(t["bg"], t["pass_"], 0.35),
        warn_bg=mix(t["bg"], t["warn"], 0.12),
        banner_bg=mix(t["bg"], t["sec"], 0.16),
        banner_border=mix(t["bg"], t["sec"], 0.4),
        banner_text=mix(t["sec"], t["text"], 0.55),
        sec_text=on_color(t["sec"]),
        scroll=mix(t["border"], t["text"], 0.15),
        sep_hover=mix(t["bg"], t["accent"], 0.35),
        arrow=_asset("arrow.svg", f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="M1.5 3.5L5 7l3.5-3.5" fill="none" stroke="{t["muted"]}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>'),
        up=_asset("up.svg", f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="M1.5 6.5L5 3l3.5 3.5" fill="none" stroke="{t["muted"]}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>'),
        check=_asset("check.svg", f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M3.5 8.5l3 3 6-7" fill="none" stroke="{on_color(t["accent"])}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>'),
    )
    return QSS.safe_substitute(d)


def apply(app, t):
    CURRENT.clear()
    CURRENT.update(t)
    pal = app.palette()
    R = QtGui.QPalette.ColorRole
    for role, key in ((R.Window, "bg"), (R.WindowText, "text"), (R.Base, "surface"), (R.AlternateBase, "surface2"),
                      (R.Text, "text"), (R.Button, "btn"), (R.ButtonText, "btn_text"), (R.Highlight, "accent"),
                      (R.ToolTipBase, "surface"), (R.ToolTipText, "text"), (R.PlaceholderText, "muted")):
        pal.setColor(role, qc(t[key]))
    pal.setColor(R.HighlightedText, qc(on_color(t["accent"])))
    app.setPalette(pal)
    app.setStyleSheet(build_qss(t))


def to_json(t):
    return json.dumps(t, ensure_ascii=False, indent=1)


def from_json(s):
    return complete(json.loads(s))
