"""The panel that opens from the menu bar item: three tabs and a footer.

The footer has Export, Preferences, Help and Quit. Under the tabs, a bar appears while items
are ticked for Move to Trash."""

import time

from PyQt6.QtCore import QEvent, QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QTabWidget, QVBoxLayout,
                             QWidget)

from . import theme
from .changes_tab import ChangesTab
from .findings_tab import FindingsTab
from .selection import Selection, TrashBar
from .status_tab import StatusTab
from .widgets import MutedLabel, small_font

WIDTH = 440
HEIGHT = 600


class Panel(QWidget):
    export_requested = pyqtSignal()
    prefs_requested = pyqtSignal()
    help_requested = pyqtSignal()
    quit_requested = pyqtSignal()

    def __init__(self, parent=None):
        # A frameless tool window rather than a Qt popup: popups close as soon as the app is
        # not the active one, which a menu bar app usually is not.
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setFixedSize(WIDTH, HEIGHT)
        self.setObjectName("panel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("#panel { border: 1px solid %s; border-radius: 8px; }" % theme.muted_css())
        self.last_hidden = 0.0
        self._was_active = False
        self.dialog_open = False   # set while Export/Preferences dialogs are up

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 6)
        lay.setSpacing(4)

        head = QHBoxLayout()
        head.setContentsMargins(14, 0, 14, 0)
        title = QLabel("DiskReport")
        title.setFont(small_font(1, bold=True))
        head.addWidget(title)
        self.machine = MutedLabel("", wrap=False)
        head.addWidget(self.machine)
        head.addStretch(1)
        lay.addLayout(head)

        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setContentsMargins(14, 0, 14, 0)
        self.banner.setStyleSheet("QLabel { color: %s; }" % theme.CAPACITY_WARN)
        self.banner.setVisible(False)
        lay.addWidget(self.banner)

        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setContentsMargins(14, 0, 14, 0)
        self.error.setStyleSheet("QLabel { color: %s; }" % theme.CAPACITY_CRITICAL)
        self.error.setVisible(False)
        lay.addWidget(self.error)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.status_tab = StatusTab()
        self.changes_tab = ChangesTab()
        self.findings_tab = FindingsTab()
        self.tabs.addTab(self.status_tab, "Status")
        self.tabs.addTab(self.changes_tab, "Changes")
        self.tabs.addTab(self.findings_tab, "Findings")
        lay.addWidget(self.tabs, 1)

        # Ticked items for Move to Trash, shared by Changes and Findings.
        self.selection = Selection(self)
        self.changes_tab.selection = self.selection
        self.findings_tab.selection = self.selection
        self.trash_bar = TrashBar(self.selection)
        lay.addWidget(self.trash_bar)
        self.status_tab.show_findings_requested.connect(lambda: self.tabs.setCurrentIndex(2))

        foot = QHBoxLayout()
        foot.setContentsMargins(10, 0, 10, 0)
        for text, sig in (("Export…", self.export_requested),
                          ("Preferences…", self.prefs_requested),
                          ("Help", self.help_requested)):
            b = QPushButton(text)
            b.setFlat(True)
            b.setFont(small_font(-1))
            b.clicked.connect(sig.emit)
            foot.addWidget(b)
        foot.addStretch(1)
        q = QPushButton("Quit")
        q.setFlat(True)
        q.setFont(small_font(-1))
        q.clicked.connect(self.quit_requested.emit)
        foot.addWidget(q)
        lay.addLayout(foot)

    def set_banner(self, text):
        self.banner.setText(text or "")
        self.banner.setVisible(bool(text))

    def set_error(self, text):
        self.error.setText(text or "")
        self.error.setVisible(bool(text))

    def rebuild(self, state):
        """Re-render every tab even if the report did not change (e.g. after a Trash move)."""
        self.changes_tab._key = None
        self.findings_tab._key = None
        self.update_from(state)

    def update_from(self, state):
        # A new scan changes sizes: drop ticks made against the old numbers.
        key = (state.current_name, state.report.now if state.report else None)
        if getattr(self, "_report_key", key) != key and self.selection.items:
            self.selection.clear()
        self._report_key = key
        self.machine.setText(state.report.machine if state.report else "")
        self.status_tab.update_from(state)
        self.changes_tab.update_from(state)
        self.findings_tab.update_from(state)

    def show_below(self, anchor):
        """Show under the menu bar item. anchor is the item's screen rect (may be empty)."""
        screen = QGuiApplication.screenAt(anchor.center()) if anchor.isValid() else None
        screen = screen or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        if anchor.isValid() and not anchor.isEmpty():
            x = anchor.center().x() - WIDTH // 2
            y = anchor.bottom() + 4
        else:
            x = area.right() - WIDTH - 8
            y = area.top() + 4
        x = max(area.left() + 4, min(x, area.right() - WIDTH - 4))
        y = max(area.top(), y)
        self.move(QPoint(x, y))
        self._was_active = False
        self.show()
        self.raise_()
        self.activateWindow()

    def changeEvent(self, event):
        # Hide when the user clicks anywhere else, like a menu.
        if event.type() == QEvent.Type.ActivationChange:
            if self.isActiveWindow():
                self._was_active = True
            elif self._was_active and not self.dialog_open:
                self.hide()
        super().changeEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)

    def hideEvent(self, event):
        self.last_hidden = time.monotonic()
        super().hideEvent(event)

