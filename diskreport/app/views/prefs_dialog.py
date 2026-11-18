"""Preferences: automatic scan schedule, notifications, open at login."""

from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                             QHBoxLayout, QMessageBox, QPushButton, QVBoxLayout)

from ...core import snapshots
from ...core.formatting import tilde
from .. import settings as settings_mod
from .widgets import MutedLabel, reveal_in_finder


class PrefsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("DiskReport Preferences")
        self.settings = dict(settings)

        lay = QVBoxLayout(self)
        form = QFormLayout()

        self.schedule = QComboBox()
        for days, label in settings_mod.SCHEDULES:
            self.schedule.addItem(label, days)
        idx = [d for d, _ in settings_mod.SCHEDULES].index(self.settings["schedule_days"]) \
            if self.settings["schedule_days"] in [d for d, _ in settings_mod.SCHEDULES] else 2
        self.schedule.setCurrentIndex(idx)
        form.addRow("Automatic scan:", self.schedule)

        self.notify = QCheckBox("Notify when a scan finds something new")
        self.notify.setChecked(self.settings["notifications"])
        form.addRow("", self.notify)
        form.addRow("", MutedLabel("Only when the disk crosses 85% full, something grew by more "
                                   "than 5 GB since the last scan, or the backup passes 14 days "
                                   "old. Nothing otherwise."))

        self.login = QCheckBox("Open DiskReport at login")
        supported, reason = settings_mod.login_item_supported()
        self.login.setChecked(settings_mod.launch_at_login_enabled())
        self.login.setEnabled(supported)
        form.addRow("", self.login)
        if not supported:
            form.addRow("", MutedLabel(reason))
        lay.addLayout(form)

        folder = QHBoxLayout()
        folder.addWidget(MutedLabel("Snapshots: %s (newest %d kept)"
                                    % (tilde(snapshots.SNAP_DIR), snapshots.SNAP_KEEP)))
        show = QPushButton("Reveal in Finder")
        show.clicked.connect(lambda: reveal_in_finder(snapshots.SNAP_DIR))
        folder.addWidget(show)
        lay.addLayout(folder)
        lay.addWidget(MutedLabel("DiskReport never erases files. It moves items to the Trash "
                                 "only when you choose Move to Trash and confirm."))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _accept(self):
        self.settings["schedule_days"] = self.schedule.currentData()
        self.settings["notifications"] = self.notify.isChecked()
        if self.login.isEnabled() and self.login.isChecked() != settings_mod.launch_at_login_enabled():
            try:
                settings_mod.set_launch_at_login(self.login.isChecked())
            except OSError as e:
                QMessageBox.warning(self, "DiskReport", "Could not change open-at-login: %s" % e)
        self.accept()

