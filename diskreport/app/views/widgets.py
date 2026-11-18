"""Small reusable widgets: chips, expandable rows, finding rows, the sparkline, Reveal in Finder.

Nothing here changes files. Reveal in Finder only selects a path in Finder; the Move to
Trash controls live in selection.py and always go through a confirmation dialog."""

import os
import subprocess

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                             QVBoxLayout, QWidget)

from ...core.formatting import date_str, fmt
from . import theme


def reveal_in_finder(path):
    """Select the path in Finder (`open -R`). Read-only: it only shows the file."""
    target = path
    while target and not os.path.lexists(target):
        target = os.path.dirname(target)
    if target:
        subprocess.Popen(["/usr/bin/open", "-R", target],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def breakable(text):
    """Let long paths wrap: a zero-width space after each / and _ gives QLabel break points."""
    return (text or "").replace("/", "/\u200b").replace("_", "_\u200b")


def small_font(delta=-2, bold=False):
    f = QFont()
    f.setPointSizeF(max(8.0, f.pointSizeF() + delta))
    if bold:
        f.setWeight(QFont.Weight.DemiBold)
    return f


class Chip(QLabel):
    """A small coloured label: a category or a severity."""

    def __init__(self, text, color, parent=None):
        super().__init__(text, parent)
        self.setFont(small_font(-3, bold=True))
        self.setStyleSheet(
            "QLabel { color: %s; border: 1px solid %s; border-radius: 4px; padding: 0px 4px; }"
            % (color, color))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)


def category_chip(category):
    return Chip(category, theme.CATEGORY_COLORS.get(category, theme.CATEGORY_COLORS["UNKNOWN"]))


def severity_chip(severity):
    return Chip(severity.upper(), theme.SEVERITY_COLORS.get(severity, theme.SEVERITY_COLORS["info"]))


class MutedLabel(QLabel):
    def __init__(self, text="", parent=None, delta=-2, wrap=True):
        super().__init__(text, parent)
        self.setFont(small_font(delta))
        self.setStyleSheet("QLabel { color: %s; }" % theme.muted_css())
        self.setWordWrap(wrap)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)


class RevealButton(QPushButton):
    def __init__(self, path, parent=None):
        super().__init__("Reveal in Finder", parent)
        self.setFont(small_font(-2))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(path)
        self.clicked.connect(lambda: reveal_in_finder(path))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)


class _ClickableHeader(QWidget):
    clicked = pyqtSignal()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class ExpandableRow(QFrame):
    """A one-line header that expands to show details. Details are built on first expand."""

    def __init__(self, header_widgets, build_details, parent=None, expanded=False):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._build_details = build_details
        self._details = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)

        self._header = _ClickableHeader()
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        h = QHBoxLayout(self._header)
        h.setContentsMargins(4, 4, 4, 4)
        h.setSpacing(6)
        self._arrow = QLabel()
        self._arrow.setFixedWidth(12)
        h.addWidget(self._arrow)
        for w in header_widgets:
            if w is None:
                h.addStretch(1)
            else:
                h.addWidget(w)
        outer.addWidget(self._header)

        self._holder = QWidget()
        self._holder_layout = QVBoxLayout(self._holder)
        self._holder_layout.setContentsMargins(22, 0, 4, 8)
        self._holder_layout.setSpacing(4)
        self._holder.setVisible(False)
        outer.addWidget(self._holder)

        self._header.clicked.connect(self.toggle)
        self._set_arrow(False)
        if expanded:
            self.toggle()

    def _set_arrow(self, open_):
        self._arrow.setText("▾" if open_ else "▸")

    def is_expanded(self):
        return self._holder.isVisible()

    def toggle(self):
        if self._details is None:
            self._details = self._build_details()
            self._holder_layout.addWidget(self._details)
        open_ = not self._holder.isVisible()
        self._holder.setVisible(open_)
        self._set_arrow(open_)


class FindingRow(QWidget):
    """One finding: category chip, size, label, detail, and Reveal in Finder if it has a path."""

    def __init__(self, finding, extra=None, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(8)

        left = QVBoxLayout()
        left.setSpacing(1)
        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(category_chip(finding.category or "UNKNOWN"))
        size = QLabel(fmt(finding.size) if finding.size is not None else "")
        size.setFont(small_font(-1, bold=True))
        size.setMinimumWidth(54 if finding.size is not None else 0)
        if finding.size is not None:
            top.addWidget(size)
        label = QLabel(breakable(finding.label))
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        top.addWidget(label, 1)
        left.addLayout(top)
        if finding.detail:
            left.addWidget(MutedLabel(breakable(finding.detail)))
        if extra is not None:
            left.addWidget(extra)
        lay.addLayout(left, 1)

        if finding.path and os.path.lexists(finding.path):
            lay.addWidget(RevealButton(finding.path), 0, Qt.AlignmentFlag.AlignTop)


def separator():
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("QFrame { color: %s; }" % theme.muted_css())
    line.setFixedHeight(1)
    return line


class Sparkline(QWidget):
    """Free space over the last snapshots, drawn with QPainter (no charting dependency)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._points = []   # [(epoch, free bytes)]
        self.setMinimumHeight(64)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_points(self, points):
        self._points = list(points)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(4, 14, -4, -14)
        muted = theme.muted()
        label_font = small_font(-3)
        p.setFont(label_font)

        if len(self._points) < 2:
            p.setPen(muted)
            p.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter,
                       "Free space history appears after two scans.")
            p.end()
            return

        xs = [e for e, _ in self._points]
        ys = [v for _, v in self._points]
        x0, x1 = min(xs), max(xs)
        lo, hi = min(ys), max(ys)
        if hi - lo < 1:
            lo, hi = lo - 1, hi + 1

        def pt(e, v):
            x = r.left() + (r.width() * ((e - x0) / (x1 - x0) if x1 > x0 else 0.5))
            y = r.bottom() - r.height() * ((v - lo) / (hi - lo))
            return QPointF(x, y)

        line = QPainterPath()
        for i, (e, v) in enumerate(self._points):
            if i == 0:
                line.moveTo(pt(e, v))
            else:
                line.lineTo(pt(e, v))

        accent = self.palette().color(self.palette().ColorRole.Highlight)
        fill = QPainterPath(line)
        fill.lineTo(QPointF(pt(xs[-1], lo).x(), r.bottom()))
        fill.lineTo(QPointF(pt(xs[0], lo).x(), r.bottom()))
        fill.closeSubpath()
        tint = QColor(accent)
        tint.setAlphaF(0.15)
        p.fillPath(fill, tint)

        p.setPen(QPen(accent, 2))
        p.drawPath(line)
        p.setBrush(accent)
        last = pt(xs[-1], ys[-1])
        p.drawEllipse(last, 3, 3)

        p.setPen(muted)
        p.drawText(QRectF(self.rect()).adjusted(4, 0, -4, 0),
                   Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight, "max %s" % fmt(hi))
        p.drawText(QRectF(self.rect()).adjusted(4, 0, -4, 0),
                   Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight, "min %s" % fmt(lo))
        p.drawText(QRectF(self.rect()).adjusted(4, 0, -4, 0),
                   Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft, date_str(x0))
        p.end()

