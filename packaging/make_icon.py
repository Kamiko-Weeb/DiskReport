"""Draw the app icon (1024x1024 PNG) and turn it into DiskReport.icns with iconutil.

    python packaging/make_icon.py [out_dir]      default out_dir: build/icon

The design: a dark rounded square with a capacity gauge, a ring most of the way full.
No text, so it stays legible at 16 px.
"""

import os
import shutil
import subprocess
import sys

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (QBrush, QColor, QGuiApplication, QLinearGradient, QPainter,
                         QPainterPath, QPen, QPixmap)

SIZE = 1024


def draw(size=SIZE):
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 1024.0

    # macOS icon grid: an 824 pt rounded square centred in 1024.
    body = QRectF(100 * s, 100 * s, 824 * s, 824 * s)
    path = QPainterPath()
    path.addRoundedRect(body, 185 * s, 185 * s)
    grad = QLinearGradient(body.topLeft(), body.bottomLeft())
    grad.setColorAt(0.0, QColor("#2B3442"))
    grad.setColorAt(1.0, QColor("#141A22"))
    p.fillPath(path, QBrush(grad))

    # Gauge: a light track and an amber-to-red arc covering about 82% of the ring.
    ring = QRectF(262 * s, 262 * s, 500 * s, 500 * s)
    width = 92 * s
    track = QPen(QColor(255, 255, 255, 46), width)
    track.setCapStyle(Qt.PenCapStyle.FlatCap)
    p.setPen(track)
    p.drawEllipse(ring)

    arc_grad = QLinearGradient(QPointF(ring.left(), ring.bottom()), QPointF(ring.right(), ring.top()))
    arc_grad.setColorAt(0.0, QColor("#F7B32B"))
    arc_grad.setColorAt(1.0, QColor("#E8590C"))
    arc = QPen(QBrush(arc_grad), width)
    arc.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(arc)
    start = 90 * 16                 # Qt angles are 1/16 degree, counter-clockwise from 3 o'clock
    span = int(-0.82 * 360 * 16)    # clockwise from 12 o'clock
    p.drawArc(ring, start, span)

    # A small centre dot, so the gauge still reads as a dial when tiny.
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255, 220))
    p.drawEllipse(QPointF(512 * s, 512 * s), 46 * s, 46 * s)
    p.end()
    return pm


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join("build", "icon")
    os.makedirs(out_dir, exist_ok=True)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QGuiApplication(sys.argv[:1])   # noqa: F841  (QPixmap needs an application)

    png = os.path.join(out_dir, "DiskReport-1024.png")
    draw().save(png)

    iconset = os.path.join(out_dir, "DiskReport.iconset")
    shutil.rmtree(iconset, ignore_errors=True)
    os.makedirs(iconset)
    for base in (16, 32, 128, 256, 512):
        draw(base).save(os.path.join(iconset, "icon_%dx%d.png" % (base, base)))
        draw(base * 2).save(os.path.join(iconset, "icon_%dx%d@2x.png" % (base, base)))
    icns = os.path.join(out_dir, "DiskReport.icns")
    subprocess.run(["iconutil", "-c", "icns", iconset, "-o", icns], check=True)
    print(icns)


if __name__ == "__main__":
    main()
