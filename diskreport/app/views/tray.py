"""The menu bar item: the capacity as text, e.g. "82%".

Normal: drawn as a template image, so macOS tints it to match the menu bar.
Above 85%: amber. Above 90%: red, and bold."""

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QIcon, QPainter, QPixmap

from . import theme

HEIGHT = 22.0   # logical points; Qt fits the image to the menu bar height


def capacity_icon(pct, scanning=False):
    text = ("%d%%" % pct) if pct is not None else "--%"
    if scanning:
        text += " …"   # thin space + ellipsis while a scan runs
    color = theme.capacity_color(pct)

    font = QFont()
    font.setPixelSize(15)
    font.setWeight(QFont.Weight.Bold if pct is not None and pct > 90 else QFont.Weight.DemiBold)
    width = QFontMetricsF(font).horizontalAdvance(text) + 4

    icon = QIcon()
    for dpr in (1.0, 2.0):
        pm = QPixmap(int(width * dpr), int(HEIGHT * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setFont(font)
        p.setPen(QColor(color) if color else QColor("black"))
        p.drawText(QRectF(0, 0, width, HEIGHT), Qt.AlignmentFlag.AlignCenter, text)
        p.end()
        icon.addPixmap(pm)
    # A template image adapts to light/dark menu bars; a coloured warning must not be one.
    icon.setIsMask(color is None)
    return icon
