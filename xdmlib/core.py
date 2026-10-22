# -*- coding: utf-8 -*-
"""Constantes, formatage SI et liaison série SCPI (OWON XDM1041 : USB COM 115200 8N1, LF)."""
import math, time, queue
import serial
from PySide6 import QtCore

APP = "OWON XDM1041 Studio"
VERSION = "3.0"
OL_LIMIT = 9.9e8

MODES = {
    "VDC":  dict(label="V DC", cmd="CONF:VOLT:DC", unit="V", ranges=[("Auto", "AUTO"), ("50 mV", "50E-3"), ("500 mV", "500E-3"), ("5 V", "5"), ("50 V", "50"), ("500 V", "500"), ("1000 V", "1000")]),
    "VAC":  dict(label="V AC", cmd="CONF:VOLT:AC", unit="V", dual=True, ranges=[("Auto", "AUTO"), ("500 mV", "500E-3"), ("5 V", "5"), ("50 V", "50"), ("500 V", "500"), ("750 V", "750")]),
    "IDC":  dict(label="A DC", cmd="CONF:CURR:DC", unit="A", ranges=[("Auto", "AUTO"), ("500 µA", "500E-6"), ("5 mA", "5E-3"), ("50 mA", "50E-3"), ("500 mA", "500E-3"), ("5 A", "5"), ("10 A", "10")]),
    "IAC":  dict(label="A AC", cmd="CONF:CURR:AC", unit="A", dual=True, ranges=[("Auto", "AUTO"), ("500 µA", "500E-6"), ("5 mA", "5E-3"), ("50 mA", "50E-3"), ("500 mA", "500E-3"), ("5 A", "5"), ("10 A", "10")]),
    "RES":  dict(label="Ω", cmd="CONF:RES", unit="Ω", ranges=[("Auto", "AUTO"), ("500 Ω", "500"), ("5 kΩ", "5E3"), ("50 kΩ", "50E3"), ("500 kΩ", "500E3"), ("5 MΩ", "5E6"), ("50 MΩ", "50E6")]),
    "CAP":  dict(label="Capa", cmd="CONF:CAP", unit="F", ranges=[("Auto", "AUTO"), ("50 nF", "50E-9"), ("500 nF", "500E-9"), ("5 µF", "5E-6"), ("50 µF", "50E-6"), ("500 µF", "500E-6"), ("5 mF", "5E-3"), ("50 mF", "50E-3")]),
    "FREQ": dict(label="Fréq", cmd="CONF:FREQ", unit="Hz", ranges=[]),
    "PER":  dict(label="Période", cmd="CONF:PER", unit="s", ranges=[]),
    "DIOD": dict(label="Diode", cmd="CONF:DIOD", unit="V", ranges=[]),
    "CONT": dict(label="Contin.", cmd="CONF:CONT", unit="Ω", ranges=[]),
    "TEMP": dict(label="Temp", cmd="CONF:TEMP:RTD", unit="°C", noprefix=True, ranges=[("PT100", "PT100"), ("KITS90", "KITS90")]),
}
FUNC_MAP = {"VOLT": "VDC", "VOLT DC": "VDC", "VOLT AC": "VAC", "CURR": "IDC", "CURR DC": "IDC", "CURR AC": "IAC",
            "RES": "RES", "CAP": "CAP", "FREQ": "FREQ", "PER": "PER", "DIOD": "DIOD", "CONT": "CONT", "TEMP": "TEMP"}
DB_REFS = ["50", "75", "93", "110", "124", "125", "135", "150", "250", "300", "500", "600", "800", "900", "1000", "1200", "8000"]
PREFIXES = [(1e9, "G"), (1e6, "M"), (1e3, "k"), (1, ""), (1e-3, "m"), (1e-6, "µ"), (1e-9, "n"), (1e-12, "p")]
SI_IN = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9}
PREFIX_WORDS = {"G": "giga", "M": "méga", "k": "kilo", "": "", "m": "milli", "µ": "micro", "n": "nano", "p": "pico"}
UNIT_WORDS = {"V": "volts", "A": "ampères", "Ω": "ohms", "F": "farads", "Hz": "hertz", "s": "secondes", "°C": "degrés",
              "°F": "degrés Fahrenheit", "K": "kelvins", "dB": "décibels", "dBm": "dBm"}


def si_split(v, noprefix=False):
    f, p = 1, ""
    if not noprefix and v != 0:
        a = abs(v)
        f, p = 1e-12, "p"
        for ff, pp in PREFIXES:
            if a >= ff * 0.99995:
                f, p = ff, pp
                break
    return v / f, p


def fmt_si(v, unit, sig=6, noprefix=False):
    if v is None:
        return "-----"
    if not math.isfinite(v):
        return "OL"
    x, p = si_split(v, noprefix)
    ax = abs(x)
    intd = 1 if ax < 1 else int(math.floor(math.log10(ax))) + 1
    return f"{x:.{max(0, sig - intd)}f} {p}{unit}"


def fmt_parts(v, unit, sig=6, noprefix=False):
    if v is None:
        return "-----", unit
    if not math.isfinite(v):
        return "OL", unit
    num, u = fmt_si(v, unit, sig, noprefix).split(" ", 1)
    return num, u


def auto_full_scale(v):
    a = abs(v)
    if a <= 0 or not math.isfinite(a):
        return None
    return 5 * 10 ** math.ceil(math.log10(a / 5) - 1e-12)


def speak_si(v, unit, sig=4, noprefix=False):
    if v is None or not math.isfinite(v):
        return "dépassement"
    x, p = si_split(v, noprefix)
    s = f"{x:.{sig}g}".replace(".", ",").replace("-", "moins ")
    return f"{s} {PREFIX_WORDS.get(p, p)} {UNIT_WORDS.get(unit, unit)}".replace("  ", " ")


def parse_si(text):
    import re
    t = str(text).strip().replace(",", ".").replace(" ", "")
    if not t:
        return None
    t = re.sub(r"(Ω|ohm|Hz|°C|°F|V|A|F|s|%)$", "", t, flags=re.I)
    if t and t[-1] in SI_IN:
        return float(t[:-1]) * SI_IN[t[-1]]
    return float(t)


def try_si(text, default=None):
    try:
        v = parse_si(text)
        return default if v is None else v
    except (ValueError, TypeError):
        return default


def parse_num(s):
    if not s:
        return None
    try:
        v = float(s.strip().strip('"').split(",")[0])
    except ValueError:
        return None
    return math.inf if abs(v) >= OL_LIMIT else v


class Stats:
    def __init__(self):
        self.reset()

    def reset(self):
        self.n, self.mean, self.m2, self.min, self.max = 0, 0.0, 0.0, math.inf, -math.inf

    def add(self, v):
        if not math.isfinite(v):
            return
        self.n += 1
        d = v - self.mean
        self.mean += d / self.n
        self.m2 += d * (v - self.mean)
        self.min, self.max = min(self.min, v), max(self.max, v)

    @property
    def std(self):
        return math.sqrt(self.m2 / (self.n - 1)) if self.n > 1 else 0.0


class DmmWorker(QtCore.QThread):
    """Boucle série : file de commandes + interrogation MEAS1?/MEAS2?, reconnexion automatique."""
    reading = QtCore.Signal(float, float, object)
    reply = QtCore.Signal(str, str)
    error = QtCore.Signal(str)
    connected = QtCore.Signal(str)
    lost = QtCore.Signal(str)

    def __init__(self, port, auto_reconnect=True):
        super().__init__()
        self.port, self.q = port, queue.Queue()
        self.running, self.polling, self.dual, self.interval = True, True, False, 0.0
        self.auto_reconnect = auto_reconnect
        self.ser = None

    def send(self, cmd, tag=None):
        self.q.put((cmd, tag))

    def _xfer(self, cmd):
        if "?" in cmd and self.ser.in_waiting:
            self.ser.reset_input_buffer()
        self.ser.write((cmd + "\n").encode("ascii"))
        if "?" in cmd:
            return self.ser.readline().decode("ascii", "ignore").strip()
        self.msleep(400 if cmd.upper().startswith(("CONF", "*RST")) else 60)
        return None

    def _open(self):
        self.ser = serial.Serial(self.port, 115200, bytesize=8, parity="N", stopbits=1, timeout=1.0, write_timeout=1)
        self.ser.reset_input_buffer()
        self._xfer("SYST:REM")
        idn = self._xfer("*IDN?") or self._xfer("*IDN?")
        if not idn:
            raise IOError("aucune réponse du multimètre")
        return idn

    def _close(self, local=False):
        if self.ser:
            try:
                if local:
                    self.ser.write(b"SYST:LOC\n")
                    self.ser.flush()
                self.ser.close()
            except Exception:
                pass
            self.ser = None

    def _loop(self):
        last, misses = 0.0, 0
        while self.running:
            try:
                while True:
                    cmd, tag = self.q.get_nowait()
                    r = self._xfer(cmd)
                    if tag:
                        self.reply.emit(tag, r or "")
            except queue.Empty:
                pass
            now = time.monotonic()
            if self.polling and now - last >= self.interval:
                last = now
                v = parse_num(self._xfer("MEAS1?"))
                s = parse_num(self._xfer("MEAS2?")) if self.dual else None
                if v is None:
                    misses += 1
                    if misses >= 6:
                        raise IOError("plus de réponse aux mesures")
                else:
                    misses = 0
                    self.reading.emit(time.time(), v, s)
            else:
                self.msleep(5)

    def run(self):
        was_connected = False
        while self.running:
            try:
                idn = self._open()
                self.connected.emit(idn)
                was_connected = True
                self._loop()
            except Exception as e:
                self._close()
                if not self.running:
                    break
                if not was_connected:
                    self.error.emit(f"Connexion à {self.port} impossible : {e}")
                    break
                if not self.auto_reconnect:
                    self.error.emit(f"Liaison perdue : {e}")
                    break
                self.lost.emit(f"Liaison perdue ({e}) — reconnexion…")
                for _ in range(20):
                    if not self.running:
                        break
                    self.msleep(100)
        self._close(local=True)
