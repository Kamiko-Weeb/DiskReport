"""Choosing items to move to the Trash: the shared selection, the per-item controls, the
bar that shows what is selected, and the confirmation dialog."""

import os

from PyQt6.QtCore import QObject, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                             QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ...core.formatting import fmt, tilde
from .. import trash
from . import theme
from .widgets import MutedLabel, breakable, category_chip, small_font


class Selection(QObject):
    """Paths ticked for the Trash, across tabs, plus what was already moved this session."""
    changed = pyqtSignal()
    trash_requested = pyqtSignal(list)      # [(path, size, category)]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = {}       # path -> (size, category)
        self.trashed = set()

    def set(self, path, on, size=None, category=None):
        if on:
            self.items[path] = (size, category)
        else:
            self.items.pop(path, None)
        self.changed.emit()

    def clear(self):
        self.items.clear()
        self.changed.emit()

    def total(self):
        return sum(s or 0 for s, _ in self.items.values())

    def request(self, items):
        self.trash_requested.emit(list(items))

    def request_selected(self):
        self.request([(p, s, c) for p, (s, c) in sorted(self.items.items())])

    def mark_trashed(self, paths):
        for p in paths:
            self.trashed.add(p)
        for p in list(self.items):
            if any(p == t or p.startswith(t + "/") for t in paths) or not os.path.lexists(p):
                self.items.pop(p, None)
        self.changed.emit()

    def is_trashed(self, path):
        """Moved this session and still gone (a rebuilt folder gets its controls back)."""
        return path in self.trashed and not os.path.lexists(path)


class _SelectBox(QCheckBox):
    """A tick box that always shows whether its path is in the selection (Clear, or ticking
    the same path in the other tab, updates it)."""

    def __init__(self, path, size, category, selection):
        super().__init__("Select")
        self._path = path
        self._selection = selection
        self.setFont(small_font(-2))
        self.setChecked(path in selection.items)
        self.toggled.connect(lambda on: selection.set(path, on, size, category))
        selection.changed.connect(self._sync)

    @pyqtSlot()
    def _sync(self):
        want = self._path in self._selection.items
        if self.isChecked() != want:
            self.blockSignals(True)
            self.setChecked(want)
            self.blockSignals(False)


def trash_controls(path, size, category, selection):
    """A checkbox for bulk selection and a Move to Trash button, or nothing if this item
    cannot be trashed. Returns a widget or None."""
    if selection is None:
        return None
    if selection.is_trashed(path):
        return MutedLabel("Moved to Trash", wrap=False)
    ok, _why = trash.can_trash(path, category)
    if not ok:
        return None
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(6)
    h.addWidget(_SelectBox(path, size, category, selection))
    b = QPushButton("Move to Trash…")
    b.setFont(small_font(-2))
    b.clicked.connect(lambda: selection.request([(path, size, category)]))
    h.addWidget(b)
    return w


class TrashBar(QWidget):
    """Shown under the tabs while anything is selected."""

    def __init__(self, selection, parent=None):
        super().__init__(parent)
        self.selection = selection
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 2, 14, 2)
        self.label = QLabel()
        h.addWidget(self.label, 1)
        clear = QPushButton("Clear")
        clear.setFont(small_font(-1))
        clear.clicked.connect(selection.clear)
        h.addWidget(clear)
        go = QPushButton("Move to Trash…")
        go.setFont(small_font(-1))
        go.clicked.connect(selection.request_selected)
        h.addWidget(go)
        selection.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        n = len(self.selection.items)
        self.label.setText("%d selected (%s)" % (n, fmt(self.selection.total())))
        self.setVisible(n > 0)


class ConfirmTrashDialog(QDialog):
    """Lists exactly what will move. Cancel is the default button."""

    def __init__(self, items, snapshots_pin_space, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Move to Trash")
        self.resize(520, 420)
        lay = QVBoxLayout(self)
        # An item inside another listed item moves with its parent: list and count it once.
        tops = set(trash.top_level([p for p, _, _ in items]))
        inner = [p for p, _, _ in items if trash.canonical(p) not in tops]
        items = [it for it in items if trash.canonical(it[0]) in tops]
        total = sum(s or 0 for _, s, _ in items)
        head = QLabel("Move %d item%s (%s) to the Trash?" % (len(items), "" if len(items) == 1 else "s",
                                                            fmt(total)))
        head.setFont(small_font(1, bold=True))
        lay.addWidget(head)

        body = QWidget()
        rows = QVBoxLayout(body)
        rows.setContentsMargins(0, 0, 0, 0)
        for path, size, cat in items:
            r = QHBoxLayout()
            r.addWidget(category_chip(cat or "UNKNOWN"))
            sz = QLabel(fmt(size) if size is not None else "")
            sz.setMinimumWidth(60)
            r.addWidget(sz)
            label = QLabel(breakable(tilde(path)))
            label.setWordWrap(True)
            r.addWidget(label, 1)
            rows.addLayout(r)
        rows.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        lay.addWidget(scroll, 1)

        notes = []
        if inner:
            notes.append("%d selected item%s inside these move with them." % (
                len(inner), " is" if len(inner) == 1 else "s are"))
        if any(c == "USER DATA" for _, _, c in items):
            warn = QLabel("Some of these are labelled USER DATA: your own files. Make sure "
                          "you have a copy of anything you want to keep.")
            warn.setWordWrap(True)
            warn.setStyleSheet("QLabel { color: %s; }" % theme.CAPACITY_CRITICAL)
            lay.addWidget(warn)
        notes.append("Items can be restored from the Trash with Finder's Put Back. The space "
                     "comes back only when you empty the Trash.")
        if snapshots_pin_space:
            notes.append("An APFS snapshot exists, so even then the space may not be freed "
                         "until the snapshot clears.")
        lay.addWidget(MutedLabel(" ".join(notes)))

        buttons = QDialogButtonBox()
        cancel = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        go = buttons.addButton("Move to Trash", QDialogButtonBox.ButtonRole.AcceptRole)
        cancel.setDefault(True)
        cancel.setAutoDefault(True)
        go.setAutoDefault(False)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        lay.addWidget(buttons)
