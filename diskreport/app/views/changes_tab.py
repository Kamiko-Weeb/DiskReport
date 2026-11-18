"""CHANGES: what moved more than 500 MB since a chosen snapshot."""

import os
import time

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout,
                             QWidget)

from ...core import snapshots
from ...core.formatting import category, date_str, fmt, fmt_signed, tilde
from ...core.scanner import THRESHOLD
from . import theme
from .selection import trash_controls
from .widgets import (ExpandableRow, MutedLabel, RevealButton, breakable, category_chip,
                      small_font)


def _snapshot_label(name, now):
    stamp = snapshots.snap_label(name)
    try:
        t = time.mktime(time.strptime(stamp, "%Y-%m-%d-%H%M"))
        days = (now - t) / 86400
        ago = "today" if days < 1 else "%d day%s ago" % (int(days), "" if int(days) == 1 else "s")
        return "%s  (%s)" % (time.strftime("%Y-%m-%d %H:%M", time.localtime(t)), ago)
    except ValueError:
        return stamp


class ChangesTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = None
        self._key = None
        self.selection = None   # set by the panel
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        row = QHBoxLayout()
        row.addWidget(QLabel("Compare with:"))
        self.combo = QComboBox()
        self.combo.currentIndexChanged.connect(self._on_choice)
        row.addWidget(self.combo, 1)
        lay.addLayout(row)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lay.addWidget(self.scroll, 1)

        self.notes = MutedLabel("")
        lay.addWidget(self.notes)

    def update_from(self, state):
        """Rebuild only when the report or snapshot list changed, so the chosen comparison and
        expanded rows survive closing and reopening the panel."""
        key = state.version_key()
        if self._state is state and key == self._key:
            return
        self._key = key
        self._state = state
        now = time.time()
        keep = self.combo.currentData()
        choices = state.compare_choices()
        default = keep if keep in choices else state.previous_name()
        self.combo.blockSignals(True)
        self.combo.clear()
        for n in choices:
            self.combo.addItem(_snapshot_label(n, now), n)
        if default in choices:
            self.combo.setCurrentIndex(choices.index(default))
        self.combo.setEnabled(bool(choices))
        self.combo.blockSignals(False)
        self._render(self.combo.currentData())

    def _on_choice(self, _index):
        if self._state is not None:
            self._render(self.combo.currentData())

    def _render(self, prev_name):
        delta, message = self._state.delta(prev_name) if self._state else (None, "No scan yet.")
        body = QWidget()
        rows = QVBoxLayout(body)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(2)
        self.notes.setText("")

        if delta is None:
            self.summary.setText(message)
        else:
            self.summary.setText(self._summary_text(delta))
            if not delta.all_rows:
                rows.addWidget(MutedLabel("No measured path changed by more than %s."
                                          % fmt(THRESHOLD)))
            shown = {r.path for r in delta.rows}
            for r in delta.all_rows:      # every path over the threshold, parents included
                rows.addWidget(self._row(r, explained=r.path not in shown))
            notes = []
            if delta.parents_omitted:
                notes.append("Greyed rows are parent folders whose change is mostly the "
                             "subfolders listed with them.")
            if delta.skipped:
                notes.append("%d path(s) not compared: unreadable or cut off in one of the "
                             "two scans." % delta.skipped)
            self.notes.setText(" ".join(notes))
        rows.addStretch(1)
        self.scroll.setWidget(body)

    def _summary_text(self, d):
        text = "Since %s" % date_str(d.prev_epoch)
        if d.projection is None:
            return text + "."
        text += ": net %s in used space" % fmt_signed(d.net)
        if d.projection == "short":
            return text + " (under a day apart, no weekly rate)."
        text += ", %s per week." % fmt_signed(d.weekly)
        if d.projection == "date":
            text += " At this rate, 95%% full on %s." % date_str(d.full_95_epoch)
        elif d.projection == "already":
            text += " Already at or above 95% full."
        else:
            text += " Flat or shrinking."
        return text

    def _row(self, r, explained=False):
        change = QLabel(fmt_signed(r.change))
        change.setFont(small_font(0, bold=True))
        change.setMinimumWidth(70)
        color = theme.GROWTH_COLOR if r.change > 0 else theme.SHRINK_COLOR
        change.setStyleSheet("QLabel { color: %s; }" % (theme.muted_css() if explained else color))
        name = QLabel(os.path.basename(r.path) or r.path)
        if explained:
            name.setStyleSheet("QLabel { color: %s; }" % theme.muted_css())
        name.setToolTip(tilde(r.path))
        sizes = MutedLabel("%s → %s%s" % (fmt(r.before), fmt(r.after), r.note), wrap=False)

        def details():
            w = QWidget()
            v = QVBoxLayout(w)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(4)
            path = QLabel(breakable(tilde(r.path)))
            path.setWordWrap(True)
            path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            v.addWidget(path)
            h = QHBoxLayout()
            h.addWidget(category_chip(category(r.path)))
            h.addStretch(1)
            if os.path.lexists(r.path):
                h.addWidget(RevealButton(r.path))
                controls = trash_controls(r.path, r.after, category(r.path), self.selection)
                if controls is not None:
                    v.addWidget(controls)
            else:
                h.addWidget(MutedLabel("No longer exists", wrap=False))
            v.addLayout(h)
            return w

        return ExpandableRow([change, name, None, sizes], details)
