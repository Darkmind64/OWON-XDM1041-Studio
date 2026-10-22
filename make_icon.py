"""Génère icon.ico (multi-tailles) à partir du dessin de l'application."""
import os, struct, io
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6 import QtWidgets, QtCore
app = QtWidgets.QApplication([])
from xdmlib.widgets import draw_icon
imgs = []
for sz in (16, 24, 32, 48, 64, 128, 256):
    ba = QtCore.QByteArray(); buf = QtCore.QBuffer(ba); buf.open(QtCore.QIODevice.WriteOnly)
    draw_icon(sz).save(buf, "PNG"); imgs.append((sz, bytes(ba)))
with open("icon.ico", "wb") as f:
    f.write(struct.pack("<HHH", 0, 1, len(imgs)))
    off = 6 + 16 * len(imgs)
    for sz, data in imgs:
        f.write(struct.pack("<BBBBHHII", sz % 256, sz % 256, 0, 0, 1, 32, len(data), off)); off += len(data)
    for _, data in imgs:
        f.write(data)
print("icon.ico OK")
