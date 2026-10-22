# -*- coding: utf-8 -*-
"""Séquences de test scriptées (exécution non bloquante pilotée par les mesures et des minuteries)."""
import math, shlex
from PySide6 import QtCore, QtWidgets
from .core import MODES, fmt_si, parse_si

HELP = """Commandes (une par ligne, # = commentaire) :
  MODE VDC|VAC|IDC|IAC|RES|CAP|FREQ|PER|DIOD|CONT|TEMP
  RANGE AUTO|<gamme ex. 5V, 50k>      RATE S|M|F
  SCPI <commande>      QUERY <requête?>      WAIT <s>
  WAIT_STABLE <tol %> <n> [timeout s]
  MEASURE <nom> [n=5] [timeout s]    (moyenne de n mesures)
  CHECK <nom> <mini> <maxi>          (ex. CHECK v5 4.75 5.25 ; notation SI acceptée)
  PROMPT "message"   LOG "texte {nom}"   SAY "texte {nom}"   BEEP
  STOP_ON_FAIL ON|OFF"""

EXAMPLE = """# Exemple : contrôle d'une alimentation 5 V
STOP_ON_FAIL OFF
PROMPT "Pointes sur la sortie 5 V, puis OK"
MODE VDC
RANGE AUTO
WAIT_STABLE 0.2 5 15
MEASURE v5 10
CHECK v5 4.75 5.25
LOG "Tension continue : {v5}"
MODE VAC
WAIT 3
MEASURE ondulation 10
CHECK ondulation 0 50m
LOG "Ondulation : {ondulation}"
SAY "Test terminé"
"""


class SeqRunner(QtCore.QObject):
    log = QtCore.Signal(str)
    done = QtCore.Signal(str)

    def __init__(self, ui):
        super().__init__()
        self.ui = ui
        self.gen = None
        self.wait = None
        self.buf = []
        self.vars = {}
        self.ok = True
        self.fail_stop = False
        self.timer = QtCore.QTimer(self, singleShot=True, timeout=self._timeout)

    @property
    def running(self):
        return self.gen is not None

    def start(self, text):
        self.stop(silent=True)
        self.vars, self.ok, self.fail_stop = {}, True, False
        self.results = []
        self.gen = self._run(text.splitlines())
        self._resume(None)

    def stop(self, silent=False):
        if self.gen:
            self.gen.close()
            self.gen = None
            self.timer.stop()
            self.wait = None
            if not silent:
                self.log.emit("■ Séquence interrompue")
                self.done.emit("ABANDON")

    def _resume(self, val):
        if not self.gen:
            return
        try:
            cond = self.gen.send(val)
        except StopIteration:
            self.gen = None
            verdict = "PASS" if self.ok else "FAIL"
            self.log.emit(f"=== Verdict : {verdict} ===")
            self.done.emit(verdict)
            return
        except Exception as e:
            self.gen = None
            self.log.emit(f"!! Erreur : {e}")
            self.done.emit("ERREUR")
            return
        self.wait, self.buf = cond, []
        k = cond[0]
        if k == "sleep":
            self.timer.start(max(1, int(cond[1] * 1000)))
        elif k in ("samples", "stable"):
            self.timer.start(int(cond[-1] * 1000))
        elif k == "query":
            self.ui.cmd(cond[1], "seq")
            self.timer.start(5000)

    def _timeout(self):
        if self.wait is not None:
            self.wait = None
            self._resume(None)

    def on_reading(self, v):
        w = self.wait
        if not w:
            return
        if w[0] == "samples":
            self.buf.append(v)
            if len(self.buf) >= w[1]:
                self.timer.stop()
                self.wait = None
                self._resume(list(self.buf))
        elif w[0] == "stable":
            tol, n = w[1], w[2]
            self.buf = (self.buf + [v])[-n:]
            if len(self.buf) == n and all(math.isfinite(x) for x in self.buf):
                m = sum(self.buf) / n
                if all(abs(x - m) <= abs(m) * tol / 100 + 1e-12 for x in self.buf):
                    self.timer.stop()
                    self.wait = None
                    self._resume(m)

    def on_reply(self, r):
        if self.wait and self.wait[0] == "query":
            self.timer.stop()
            self.wait = None
            self._resume(r)

    def _f(self, v):
        return fmt_si(v, self.ui.unit(), self.ui.sp_dig.value(), self.ui.noprefix())

    def _fmt(self, s):
        try:
            return s.format(**{k: self._f(v) for k, v in self.vars.items()})
        except (KeyError, IndexError, ValueError):
            return s

    def _run(self, lines):
        self.log.emit("▶ Démarrage de la séquence")
        for no, raw in enumerate(lines, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            c, rest = parts[0].upper(), (parts[1] if len(parts) > 1 else "")
            try:
                a = shlex.split(rest)
            except ValueError:
                a = rest.split()
            self.log.emit(f"[{no}] {line}")
            if c == "SCPI":
                self.ui.cmd(rest)
                yield ("sleep", 0.15)
            elif c == "QUERY":
                r = yield ("query", rest)
                self.log.emit(f"    << {r if r is not None else '(pas de réponse)'}")
            elif c == "MODE":
                key = a[0].upper()
                if key not in MODES:
                    raise ValueError(f"mode inconnu : {key}")
                self.ui.set_mode(key)
                yield ("sleep", 1.2)
            elif c == "RANGE":
                if not self.ui.select_range(a[0]):
                    raise ValueError(f"gamme inconnue : {a[0]}")
                yield ("sleep", 0.8)
            elif c == "RATE":
                idx = {"S": 0, "L": 0, "M": 1, "F": 2}[a[0].upper()[0]]
                self.ui.cb_speed.setCurrentIndex(idx)
                self.ui.set_speed(idx)
                yield ("sleep", 0.3)
            elif c == "WAIT":
                yield ("sleep", float(a[0]))
            elif c == "WAIT_STABLE":
                tol, n = float(a[0]), int(a[1])
                to = float(a[2]) if len(a) > 2 else 30
                m = yield ("stable", tol, n, to)
                if m is None:
                    self.ok = False
                    self.log.emit("    ✘ mesure instable (délai dépassé)")
                    if self.fail_stop:
                        return
                else:
                    self.log.emit(f"    stable : {self._f(m)}")
            elif c == "MEASURE":
                name = a[0]
                n = int(a[1]) if len(a) > 1 else 5
                to = float(a[2]) if len(a) > 2 else 30
                vals = yield ("samples", n, to)
                fin = [x for x in (vals or []) if math.isfinite(x)]
                v = sum(fin) / len(fin) if vals and len(fin) == len(vals) else math.inf
                if not vals:
                    self.ok = False
                    self.log.emit("    ✘ aucune mesure reçue")
                    if self.fail_stop:
                        return
                self.vars[name] = v
                self.log.emit(f"    {name} = {self._f(v)}")
            elif c == "CHECK":
                name, lo, hi = a[0], parse_si(a[1]), parse_si(a[2])
                v = self.vars.get(name)
                good = v is not None and math.isfinite(v) and lo <= v <= hi
                self.results.append((name, self._f(v) if v is not None else "—", a[1], a[2], "PASS" if good else "FAIL"))
                self.log.emit(f"    {'✔' if good else '✘'} {name} = {self._f(v) if v is not None else '—'}  [{a[1]} … {a[2]}]")
                if not good:
                    self.ok = False
                    if self.fail_stop:
                        return
            elif c == "PROMPT":
                r = QtWidgets.QMessageBox.question(self.ui, "Séquence", self._fmt(rest.strip('"')),
                                                   QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel)
                if r != QtWidgets.QMessageBox.Ok:
                    self.ok = False
                    self.log.emit("    abandon opérateur")
                    return
            elif c == "LOG":
                self.log.emit("    " + self._fmt(rest.strip('"')))
            elif c == "SAY":
                self.ui.speaker.say(self._fmt(rest.strip('"')))
            elif c == "BEEP":
                self.ui.beep("ok", "seq")
            elif c == "STOP_ON_FAIL":
                self.fail_stop = a[0].upper() in ("ON", "1", "OUI")
            else:
                raise ValueError(f"commande inconnue ligne {no} : {c}")
