# -*- coding: utf-8 -*-
"""Analyses : séries E, consommation veille/réveil, détection d'anomalies, décharge batterie."""
import math
from collections import deque
import numpy as np

SERIES = {
    "E6": [1.0, 1.5, 2.2, 3.3, 4.7, 6.8],
    "E12": [1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2],
    "E24": [1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0, 3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1],
    "E48": [round(10 ** (i / 48), 2) for i in range(48)],
    "E96": [round(10 ** (i / 96), 2) for i in range(96)],
}


def nearest_e(v, series):
    if v is None or not math.isfinite(v) or v <= 0:
        return None, None
    d = math.floor(math.log10(v))
    best = None
    for dd in (d - 1, d, d + 1):
        for b in SERIES[series]:
            n = b * 10 ** dd
            err = abs(math.log(v / n))
            if best is None or err < best[0]:
                best = (err, n)
    nom = float(f"{best[1]:.3g}")
    return nom, (v - nom) / nom * 100


def auto_threshold(y):
    y = np.asarray(y, float)
    y = y[np.isfinite(y)]
    if y.size < 3:
        return None
    lo, hi = np.percentile(y, 10), np.percentile(y, 90)
    return math.sqrt(lo * hi) if lo > 0 and hi > 0 else (lo + hi) / 2


def consumption(t, y, thr):
    t, y = np.asarray(t, float), np.asarray(y, float)
    m = np.isfinite(y)
    t, y = t[m], y[m]
    if y.size < 3:
        return None
    dt = np.diff(t)
    dt = np.append(dt, dt[-1] if dt.size else 0.0)
    total = dt.sum()
    if total <= 0:
        return None
    hi = y > thr
    lo = ~hi
    rising = np.flatnonzero(lo[:-1] & hi[1:]) + 1
    nw = len(rising)
    return dict(
        duration=total, avg=float((y * dt).sum() / total),
        sleep=float((y[lo] * dt[lo]).sum() / dt[lo].sum()) if lo.any() and dt[lo].sum() > 0 else math.nan,
        wake=float((y[hi] * dt[hi]).sum() / dt[hi].sum()) if hi.any() and dt[hi].sum() > 0 else math.nan,
        duty=float(dt[hi].sum() / total * 100), wakes=nw,
        period=float(np.diff(t[rising]).mean()) if nw > 1 else math.nan,
        wake_dur=float(dt[hi].sum() / nw) if nw else math.nan,
        peak=float(y.max()), min=float(y.min()))


class AnomalyDetector:
    def __init__(self):
        self.reset()

    def reset(self):
        self.win = deque(maxlen=50)
        self.tw = deque()
        self.last_ev = {}
        self.prev_ol = self.prev_low = False
        self.last_drift = 0.0

    def feed(self, t, v, cfg):
        ev = []

        def emit(kind, msg, gap):
            if t - self.last_ev.get(kind, -1e18) >= gap:
                self.last_ev[kind] = t
                ev.append((kind, msg))

        fin = math.isfinite(v)
        if cfg["ol"] and not fin and not self.prev_ol:
            emit("Dépassement", "OL / circuit ouvert", 0)
        self.prev_ol = not fin
        if not fin:
            return ev
        low = cfg.get("low")
        if low is not None:
            is_low = abs(v) < low
            if is_low and not self.prev_low:
                emit("Décrochage", "valeur sous le seuil", 0)
            self.prev_low = is_low
        if cfg["spike"]:
            n = cfg["n"]
            if self.win.maxlen != n:
                self.win = deque(self.win, maxlen=n)
            if len(self.win) >= max(5, n // 2):
                arr = np.fromiter(self.win, float)
                med = float(np.median(arr))
                sd = max(float(np.median(np.abs(arr - med))) * 1.4826, abs(med) * 1e-5, 1e-15)
                z = (v - med) / sd
                if abs(z) > cfg["k"]:
                    emit("Pic", f"{z:+.1f} σ par rapport à la médiane", 1.0)
            self.win.append(v)
        if cfg["drift"]:
            W = cfg["drift_win"]
            self.tw.append((t, v))
            while self.tw and t - self.tw[0][0] > W:
                self.tw.popleft()
            if t - self.last_drift >= 5 and len(self.tw) > 10 and t - self.tw[0][0] > W * 0.5:
                self.last_drift = t
                a = np.array(self.tw)
                slope = np.polyfit(a[:, 0] - a[0, 0], a[:, 1], 1)[0]
                m = float(a[:, 1].mean())
                if m:
                    rate = slope * 60 / abs(m) * 100
                    if abs(rate) > cfg["drift_thr"]:
                        emit("Dérive", f"{rate:+.3f} %/min", 30)
        return ev


class Battery:
    def __init__(self):
        self.v0 = None
        self.reset()

    def reset(self):
        self.running = False
        self.ah = self.wh = 0.0
        self.t0 = self.last_t = None
        self.pts = []
        self.rint = None
        self.done_reason = None
        self.kind, self.load, self.cutoff = 0, 1.0, None

    def start(self, kind, load, cutoff):
        v0 = self.v0
        self.reset()
        self.v0 = v0
        self.kind, self.load, self.cutoff = kind, load, cutoff
        self.running = True

    def current(self, v):
        return self.load if self.kind == 0 else (v / self.load if self.load else 0.0)

    def feed(self, t, v):
        if not self.running or not math.isfinite(v):
            return False
        if self.last_t is None:
            self.t0 = t
        else:
            dt = t - self.last_t
            i = self.current(v)
            self.ah += i * dt / 3600
            self.wh += v * i * dt / 3600
        self.last_t = t
        self.pts.append((t - self.t0, self.ah, v))
        if self.cutoff is not None and v <= self.cutoff:
            self.running = False
            self.done_reason = "tension de coupure atteinte"
            return True
        return False

    def measure_rint(self, v_load):
        if self.v0 is None or v_load is None or not math.isfinite(v_load):
            return None
        i = self.current(v_load)
        if i > 0:
            self.rint = (self.v0 - v_load) / i
        return self.rint
