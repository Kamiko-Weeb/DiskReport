"""Colours. Mid-tone colours used as text and borders, so they read on light and dark
backgrounds without separate palettes."""

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

SEVERITY_COLORS = {
    "warning": "#E03131",
    "notice": "#E8890C",
    "info": "#7C8791",
}

CATEGORY_COLORS = {
    "CACHE": "#12A39A",
    "REBUILDABLE": "#3BA55C",
    "SYSTEM": "#7C8791",
    "USER DATA": "#3B82F6",
    "OTHER ACCOUNT": "#D6336C",
    "UNKNOWN": "#9A8F7E",
}

CAPACITY_WARN = "#E8890C"      # above 85%
CAPACITY_CRITICAL = "#E03131"  # above 90%
GROWTH_COLOR = "#E03131"       # a folder that grew
SHRINK_COLOR = "#3BA55C"       # a folder that shrank


def capacity_color(pct):
    """Colour for a capacity percentage, or None when it is fine."""
    if pct is None:
        return None
    if pct > 90:
        return CAPACITY_CRITICAL
    if pct > 85:
        return CAPACITY_WARN
    return None


def is_dark():
    pal = QApplication.palette()
    return pal.color(QPalette.ColorRole.Window).lightness() < 128


def muted():
    """Secondary text colour for the current appearance."""
    pal = QApplication.palette()
    c = pal.color(QPalette.ColorRole.WindowText)
    c.setAlphaF(0.6)
    return c


def muted_css():
    c = muted()
    return "rgba(%d,%d,%d,%.2f)" % (c.red(), c.green(), c.blue(), c.alphaF())


def qcolor(hex_color):
    return QColor(hex_color)
