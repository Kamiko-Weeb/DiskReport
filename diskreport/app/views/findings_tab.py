"""FINDINGS: the targeted checks, rendered from the shared CheckResult shape only.

Any check that core.checks adds shows up here with no changes to this file."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QGridLayout, QLabel, QScrollArea, QVBoxLayout,
                             QWidget)

from ...core.formatting import CATEGORIES, fmt
from . import theme
from .selection import trash_controls
from .widgets import (Chip, ExpandableRow, FindingRow, MutedLabel, category_chip,
                      separator, severity_chip, small_font)

SEVERITY_RANK = {"warning": 0, "notice": 1, "info": 2}
STATE_TEXT = {
    "quiet": "NOTHING FOUND",
    "unavailable": "COULD NOT CHECK",
    "absent": "NOT ON THIS MAC",
}


ACRONYMS = {"OS", "APFS", "UAF", "AI", "VM"}
SMALL_WORDS = {"AND", "OF", "OR", "THE", "IN", "ON"}


def _title(name):
    """'STAGED OS UPDATES' -> 'Staged OS Updates'."""
    if not name.isupper():
        return name
    words = name.split(" ")
    return " ".join(w if w in ACRONYMS else (w.lower() if i and w in SMALL_WORDS else w.capitalize())
                    for i, w in enumerate(words))


class FindingsTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = None
        self._key = None
        self.selection = None   # set by the panel
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lay.addWidget(self.scroll, 1)

        self.show_all = QCheckBox("Show all checks")
        self.show_all.toggled.connect(lambda _: self._render())
        lay.addWidget(self.show_all)

        lay.addWidget(separator())

        def legend():
            w = QWidget()
            grid = QGridLayout(w)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(3)
            for i, (cat, meaning) in enumerate(CATEGORIES.items()):
                grid.addWidget(category_chip(cat), i, 0)
                grid.addWidget(MutedLabel(meaning, wrap=False), i, 1)
            grid.setColumnStretch(1, 1)
            return w

        lay.addWidget(ExpandableRow([MutedLabel("What the labels mean", wrap=False), None],
                                    legend))

    def update_from(self, state):
        """Rebuild only when the report changed, so expanded rows survive reopening."""
        key = state.version_key()
        if self._state is state and key == self._key:
            return
        self._key = key
        self._state = state
        self._render()

    def _render(self):
        body = QWidget()
        rows = QVBoxLayout(body)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(2)
        report = self._state.report if self._state else None

        if report is None:
            self.summary.setText("No scan yet. Use Scan Now on the Status tab.")
        elif not report.has_results:
            self.summary.setText("This snapshot was saved by an older version and has no "
                                 "check results. Scan Now to see findings.")
        else:
            found = sorted((r for r in report.results if r.state == "found"),
                           key=lambda r: (SEVERITY_RANK.get(r.severity, 3), r.number))
            # Checks that could not run are always shown: "could not check" is not "nothing found".
            failed = [r for r in report.results if r.state == "unavailable"]
            others = [r for r in report.results if r.state in ("quiet", "absent")]
            if found:
                self.summary.setText("%d of %d checks found something." % (len(found),
                                                                           len(report.results)))
            else:
                self.summary.setText("No check found anything over its threshold.")
            for r in found + failed:
                rows.addWidget(self._row(r))
            if self.show_all.isChecked():
                for r in others:
                    rows.addWidget(self._row(r))
            elif others:
                rows.addWidget(MutedLabel("%d more check%s with nothing to show."
                                          % (len(others), "" if len(others) == 1 else "s")))
        rows.addStretch(1)
        self.scroll.setWidget(body)

    def _row(self, r):
        if r.state == "found":
            chip = severity_chip(r.severity)
        else:
            chip = Chip(STATE_TEXT.get(r.state, r.state.upper()), theme.muted_css())
        name = QLabel(_title(r.name))
        name.setFont(small_font(0, bold=True))
        size = QLabel(fmt(r.total) if r.total else "")
        size.setFont(small_font(0, bold=True))

        def details():
            w = QWidget()
            v = QVBoxLayout(w)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(2)
            if r.unavailable:
                warn = QLabel("Could not fully check: " + r.unavailable)
                warn.setWordWrap(True)
                warn.setStyleSheet("QLabel { color: %s; }" % theme.CAPACITY_WARN)
                v.addWidget(warn)
            for f in r.findings:
                extra = trash_controls(f.path, f.size, f.category, self.selection) if f.path else None
                v.addWidget(FindingRow(f, extra))
            if not r.findings and r.state != "found":
                v.addWidget(MutedLabel({"quiet": "Nothing over the threshold.",
                                        "absent": "Not present on this Mac."}.get(r.state, "")))
            if r.note:
                note = MutedLabel(r.note)
                f = note.font()
                f.setItalic(True)
                note.setFont(f)
                v.addWidget(note)
            return w

        return ExpandableRow([chip, name, None, size], details)
