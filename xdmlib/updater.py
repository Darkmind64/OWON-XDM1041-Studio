# -*- coding: utf-8 -*-
"""Mises à jour automatiques via les releases GitHub."""
import os, sys, re, json, ssl, time, hashlib, tempfile, subprocess, urllib.request
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

REPO = "Darkmind64/OWON-XDM1041-Studio"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE = f"https://github.com/{REPO}/releases/latest"
UA = {"User-Agent": "XDM1041Studio-updater", "Accept": "application/vnd.github+json"}


def vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:4]) or (0,)


def _ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def install_kind():
    """'installed' (Inno Setup), 'portable' (exe unique) ou 'source'."""
    if not getattr(sys, "frozen", False):
        return "source"
    d = os.path.dirname(sys.executable)
    if any(f.lower().startswith("unins") and f.lower().endswith(".exe") for f in os.listdir(d)):
        return "installed"
    if os.path.isdir(os.path.join(d, "_internal")):
        return "folder"
    return "portable"


def pick_asset(rel, kind):
    key = "setup" if kind == "installed" else "portable"
    for a in rel.get("assets", []):
        n = a.get("name", "").lower()
        if key in n and n.endswith(".exe"):
            return a
    return None


class Checker(QtCore.QThread):
    result = QtCore.Signal(object, str)

    def run(self):
        try:
            req = urllib.request.Request(API, headers=UA)
            with urllib.request.urlopen(req, timeout=10, context=_ctx()) as r:
                self.result.emit(json.loads(r.read().decode("utf-8")), "")
        except Exception as e:
            self.result.emit(None, str(e))


class Downloader(QtCore.QThread):
    progress = QtCore.Signal(int, int)
    done = QtCore.Signal(str, str)

    def __init__(self, url, dest, digest=None):
        super().__init__()
        self.url, self.dest, self.digest, self.cancel = url, dest, digest, False

    def run(self):
        try:
            req = urllib.request.Request(self.url, headers={"User-Agent": UA["User-Agent"], "Accept": "application/octet-stream"})
            h = hashlib.sha256()
            with urllib.request.urlopen(req, timeout=30, context=_ctx()) as r, open(self.dest + ".part", "wb") as f:
                total, got = int(r.headers.get("Content-Length") or 0), 0
                while not self.cancel:
                    b = r.read(256 * 1024)
                    if not b:
                        break
                    f.write(b)
                    h.update(b)
                    got += len(b)
                    self.progress.emit(got, total)
            if self.cancel:
                os.remove(self.dest + ".part")
                self.done.emit("", "annulé")
                return
            if self.digest and self.digest.startswith("sha256:") and h.hexdigest() != self.digest[7:].lower():
                os.remove(self.dest + ".part")
                self.done.emit("", "somme de contrôle SHA-256 invalide")
                return
            os.replace(self.dest + ".part", self.dest)
            self.done.emit(self.dest, "")
        except Exception as e:
            self.done.emit("", str(e))


class UpdateDialog(QtWidgets.QDialog):
    def __init__(self, parent, rel, current):
        super().__init__(parent)
        self.rel = rel
        self.choice = None
        self.setWindowTitle("Mise à jour disponible")
        self.resize(620, 520)
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 18)
        v.setSpacing(12)
        t = QtWidgets.QLabel(f"Version {rel.get('tag_name', '').lstrip('vV')} disponible", objectName="Title")
        f = t.font()
        f.setPointSizeF(f.pointSizeF() * 1.6)
        f.setBold(True)
        t.setFont(f)
        v.addWidget(t)
        v.addWidget(QtWidgets.QLabel(f"Version installée : {current}", objectName="Muted"))
        notes = QtWidgets.QTextBrowser(openExternalLinks=True)
        notes.setMarkdown(rel.get("body") or "_Pas de notes de version._")
        v.addWidget(notes, 1)
        self.bar = QtWidgets.QProgressBar()
        self.bar.hide()
        self.status = QtWidgets.QLabel("", objectName="Muted")
        v.addWidget(self.bar)
        v.addWidget(self.status)
        row = QtWidgets.QHBoxLayout()
        self.bt_skip = QtWidgets.QPushButton("Ignorer cette version")
        self.bt_later = QtWidgets.QPushButton("Plus tard")
        self.bt_go = QtWidgets.QPushButton("Installer maintenant", objectName="Primary")
        self.bt_skip.clicked.connect(lambda: self._end("skip"))
        self.bt_later.clicked.connect(self.reject)
        self.bt_go.clicked.connect(lambda: self._end("install"))
        row.addWidget(self.bt_skip)
        row.addStretch()
        row.addWidget(self.bt_later)
        row.addWidget(self.bt_go)
        v.addLayout(row)

    def _end(self, c):
        self.choice = c
        self.accept()


class Updater(QtCore.QObject):
    """Vérifie, télécharge et applique les mises à jour."""

    def __init__(self, main, current):
        super().__init__(main)
        self.main, self.current = main, current
        self.cfg = main.cfg
        self.checker = self.dl = None
        self.silent = True

    def check(self, silent=True):
        if self.checker and self.checker.isRunning():
            return
        self.silent = silent
        self.checker = Checker()
        self.checker.result.connect(self._on_result)
        self.checker.start()
        if not silent:
            self.main.statusBar().showMessage("Recherche de mises à jour…", 4000)

    def auto_check(self):
        last = float(self.cfg.value("upd_last", 0) or 0)
        if time.time() - last > 6 * 3600:
            self.check(silent=True)

    def _on_result(self, rel, err):
        self.cfg.setValue("upd_last", time.time())
        if rel is None:
            if not self.silent:
                QtWidgets.QMessageBox.warning(self.main, "Mises à jour", f"Impossible de joindre GitHub :\n{err}")
            return
        tag = rel.get("tag_name", "")
        if rel.get("draft") or vtuple(tag) <= vtuple(self.current):
            if not self.silent:
                QtWidgets.QMessageBox.information(self.main, "Mises à jour", f"Vous utilisez la dernière version ({self.current}).")
            return
        if self.silent and self.cfg.value("upd_skip", "") == tag:
            return
        d = UpdateDialog(self.main, rel, self.current)
        if d.exec() != QtWidgets.QDialog.Accepted:
            return
        if d.choice == "skip":
            self.cfg.setValue("upd_skip", tag)
            return
        self.install(rel)

    def install(self, rel):
        kind = install_kind()
        asset = pick_asset(rel, kind) if kind in ("installed", "portable") else None
        if asset is None:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(rel.get("html_url") or PAGE))
            return
        dest = os.path.join(tempfile.gettempdir(), asset["name"])
        prog = QtWidgets.QProgressDialog(f"Téléchargement de {asset['name']}…", "Annuler", 0, 100, self.main)
        prog.setWindowTitle("Mise à jour")
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)
        prog.setValue(0)
        self.dl = Downloader(asset["browser_download_url"], dest, asset.get("digest"))
        prog.canceled.connect(lambda: setattr(self.dl, "cancel", True))

        def on_prog(got, total):
            if total:
                prog.setValue(int(got * 100 / total))
                prog.setLabelText(f"Téléchargement de {asset['name']}…  {got / 1e6:.1f} / {total / 1e6:.1f} Mo")

        def on_done(path, err):
            prog.reset()
            if not path:
                if err != "annulé":
                    QtWidgets.QMessageBox.warning(self.main, "Mise à jour", f"Échec du téléchargement : {err}")
                return
            self.apply(path, kind)

        self.dl.progress.connect(on_prog)
        self.dl.done.connect(on_done)
        self.dl.start()

    def apply(self, path, kind):
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        if kind == "installed":
            args = [path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"]
            local = os.environ.get("LOCALAPPDATA", "")
            if local and os.path.normcase(sys.executable).startswith(os.path.normcase(local)):
                args.append("/CURRENTUSER")
            try:
                subprocess.Popen(args, creationflags=flags, close_fds=True)
            except OSError as e:
                QtWidgets.QMessageBox.warning(self.main, "Mise à jour", f"Impossible de lancer l'installateur : {e}")
                return
        else:
            exe = sys.executable
            bat = os.path.join(tempfile.gettempdir(), "xdm1041_update.cmd")
            with open(bat, "w", encoding="mbcs") as f:
                f.write("@echo off\r\n"
                        f":w\r\ntasklist /FI \"PID eq {os.getpid()}\" | find \"{os.getpid()}\" >nul && (timeout /t 1 /nobreak >nul & goto w)\r\n"
                        f"move /y \"{path}\" \"{exe}\" >nul\r\n"
                        f"start \"\" \"{exe}\"\r\n"
                        "del \"%~f0\"\r\n")
            subprocess.Popen(["cmd", "/c", bat], creationflags=flags | 0x08000000, close_fds=True)
        self.main.close()
        QtWidgets.QApplication.instance().quit()
