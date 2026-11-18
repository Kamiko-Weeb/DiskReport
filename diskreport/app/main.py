"""DiskReport menu bar app: entry point and controller.

    python3 -m diskreport.app

The menu bar item shows the disk's capacity; clicking it opens the panel. Scans run on a
background thread, so the UI never waits on du. The app never erases anything; its only
change to your files is Move to Trash, for items you pick and confirm (see trash.py).
"""

import os
import sys
import time
import traceback

from PyQt6.QtCore import QObject, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox, QSystemTrayIcon

from . import APP_NAME
from . import settings as settings_mod
from .state import AppState, notify_reasons
from .views.panel import Panel
from .views.help_dialog import HelpDialog
from .views.prefs_dialog import PrefsDialog
from .views.selection import ConfirmTrashDialog
from . import trash
from ..core.formatting import fmt, tilde
from .views.tray import capacity_icon
from ..cli.main import report_text
from ..core import snapshots
from ..core.report import run_report

SCHEDULE_CHECK_MS = 30 * 60 * 1000     # how often to see whether a scheduled scan is due
REOPEN_GUARD_S = 0.3                   # a click on the menu bar item that just closed the panel


class ScanThread(QThread):
    """Runs one full scan off the UI thread, then saves and prunes snapshots."""
    progress = pyqtSignal(str, int, int)
    finished_ok = pyqtSignal(object, object, object)   # report, saved name, (prev name, prev snapshot)
    failed = pyqtSignal(str)

    def __init__(self, prev, parent=None):
        super().__init__(parent)
        self.prev = prev     # (name, snapshot) of the newest snapshot before this scan, or None

    def run(self):
        try:
            report = run_report(progress=lambda s, d, t: self.progress.emit(s, d, t))
            saved = snapshots.save_snapshot(report.snapshot, report.now)
            snapshots.prune_snapshots()
            self.finished_ok.emit(report, saved, self.prev)
        except Exception:      # report it in the UI instead of dying silently
            self.failed.emit(traceback.format_exc())


class DiskReportApp(QObject):
    def __init__(self, qapp):
        super().__init__()
        self.qapp = qapp
        self.settings = settings_mod.load()
        self.state = AppState()
        self.scan_thread = None
        self.help_dialog = None

        self.panel = Panel()
        self.panel.status_tab.scan_requested.connect(lambda: self.start_scan(manual=True))
        self.panel.selection.trash_requested.connect(self.trash_items)
        self.panel.export_requested.connect(self.export)
        self.panel.prefs_requested.connect(self.preferences)
        self.panel.help_requested.connect(self.show_help)
        self.panel.quit_requested.connect(self.qapp.quit)
        self.qapp.aboutToQuit.connect(self._about_to_quit)

        # Start with a system-symbol icon: Qt then gives the menu bar item a variable width
        # and keeps it when the text icon replaces it. With a plain image Qt uses a fixed
        # square item and shrinks "93%" to fit (qcocoasystemtrayicon.mm, Qt 6.12).
        self.tray = QSystemTrayIcon(QIcon.fromTheme(QIcon.ThemeIcon.DriveHarddisk))
        self.background_launch = "--background" in sys.argv   # started at login
        self.qapp.applicationStateChanged.connect(self._on_app_state)
        self.tray.setToolTip(APP_NAME)
        self.tray.activated.connect(self._on_tray)
        self.tray.messageClicked.connect(self._show_findings)
        self._manual_scan = False

        self.schedule_timer = QTimer(self)
        self.schedule_timer.setInterval(SCHEDULE_CHECK_MS)
        self.schedule_timer.timeout.connect(self._scheduled_check)

    # ------------------------------------------------------------------
    def start(self):
        self.tray.show()
        if not self.background_launch:
            QTimer.singleShot(400, self._open_from_launch)
        self._show_launch_banner()
        self.state.load_latest()
        self.refresh()
        # Show the last snapshot instantly; scan only if it is old (or there is none).
        if self.state.needs_scan(settings_mod.RESCAN_ON_LAUNCH_DAYS):
            QTimer.singleShot(500, self.start_scan)
        self.schedule_timer.start()

    def refresh(self):
        self.tray.setIcon(capacity_icon(self.state.capacity, scanning=self.scanning))
        cap = self.state.capacity
        self.tray.setToolTip("%s: %s" % (APP_NAME, ("%d%% full" % cap) if cap is not None else "no scan yet"))
        self.panel.update_from(self.state)

    @property
    def scanning(self):
        return self.scan_thread is not None and self.scan_thread.isRunning()

    def _show_launch_banner(self):
        if settings_mod.is_translocated() or settings_mod.is_on_disk_image():
            self.panel.set_banner(
                "DiskReport is running from a temporary read-only location macOS uses for "
                "downloaded apps. It works, but to keep it (and to open it at login), drag "
                "DiskReport.app into Applications and open it from there.")

    # ------------------------------------------------------------------
    def _open_from_launch(self):
        self.panel.update_from(self.state)
        self.panel.show_below(self.tray.geometry())

    def _on_app_state(self, state):
        # Opening DiskReport again from Applications or Launchpad activates the running app,
        # which has no window: show the panel so the launch visibly does something.
        if state != Qt.ApplicationState.ApplicationActive or self.background_launch:
            return
        if self.panel.isVisible() or any(w.isVisible() for w in QApplication.topLevelWidgets()
                                         if w is not self.panel):
            return
        if time.monotonic() - self.panel.last_hidden < 1.0:
            return
        self._open_from_launch()

    def _on_tray(self, reason):
        # On macOS 27, Qt can report a plain click on a menu bar item as Unknown (status bar
        # buttons are driven by gesture recognizers there), so Unknown counts as a click.
        if reason not in (QSystemTrayIcon.ActivationReason.Trigger,
                          QSystemTrayIcon.ActivationReason.DoubleClick,
                          QSystemTrayIcon.ActivationReason.Unknown):
            return
        if self.panel.isVisible():
            self.panel.hide()
            return
        if time.monotonic() - self.panel.last_hidden < REOPEN_GUARD_S:
            return    # this click is the one that just closed the panel
        self.panel.update_from(self.state)
        self.panel.show_below(self.tray.geometry())

    # ------------------------------------------------------------------
    def start_scan(self, manual=False):
        if self.scanning:
            return
        self._manual_scan = manual
        self.scan_thread = ScanThread(snapshots.latest())
        self.scan_thread.progress.connect(self.panel.status_tab.set_progress)
        self.scan_thread.finished_ok.connect(self._scan_done)
        self.scan_thread.failed.connect(self._scan_failed)
        self.scan_thread.finished.connect(self._scan_thread_ended)
        self.panel.status_tab.set_scanning(True)
        self.scan_thread.start()
        self.tray.setIcon(capacity_icon(self.state.capacity, scanning=True))

    def _about_to_quit(self):
        # A scan in progress has nothing worth saving yet. Without this, Python would wait
        # for every queued du job to finish before the process exits, invisibly.
        if self.scanning:
            self.tray.hide()
            os._exit(0)

    def _scan_done(self, report, saved, prev):
        self.panel.set_error("")
        self.state.set_fresh(report, saved)
        prev_name, prev_snapshot = prev if prev else (None, None)
        if prev_name is not None and prev_name == saved:
            # A same-minute rescan replaced that file: compare with the one before it instead.
            prev_name, prev_snapshot = snapshots.previous_before(report.now, exclude=saved)
        delta = None
        if prev_snapshot:
            delta = snapshots.compute_delta(report.snapshot, prev_snapshot, prev_name, report.now)
        try:   # the plain-text copy other tools (and Claude) can read without rescanning
            snapshots.save_latest_report(report_text(report, prev_snapshot, prev_name, delta))
        except OSError:
            pass

        found = sum(1 for r in report.results if r.state == "found")
        when = time.strftime("%H:%M", time.localtime(report.now))
        if found:
            msg = ("Scan complete (%s). %d of %d checks found something: look in the Findings "
                   "tab." % (when, found, len(report.results)))
        else:
            msg = "Scan complete (%s). No check found anything over its threshold." % when
        self.panel.status_tab.show_message(msg)

        reasons = notify_reasons(prev_snapshot, report, delta) if self.settings["notifications"] else []
        if reasons:
            self.tray.showMessage(APP_NAME, " ".join(reasons),
                                  QSystemTrayIcon.MessageIcon.Information, 10000)
        elif self._manual_scan and not self.panel.isVisible():
            # You asked for this scan, so say when it is done even with the panel closed.
            self.tray.showMessage("Scan complete", msg + " Click to open it.",
                                  QSystemTrayIcon.MessageIcon.Information, 10000)

    def _show_findings(self):
        self.panel.update_from(self.state)
        self.panel.tabs.setCurrentIndex(2)
        if not self.panel.isVisible():
            self.panel.show_below(self.tray.geometry())

    # ------------------------------------------------------------------
    def trash_items(self, items):
        """Confirm, then move the listed items to the Trash: the only change the app makes to
        your own files."""
        snap = self.state.check_data("snapshots") or {}
        self.panel.dialog_open = True      # keep the panel open behind the dialog
        try:
            dlg = ConfirmTrashDialog(items, bool(snap.get("pins_user_data")))
            dlg.raise_()
            dlg.activateWindow()
            accepted = dlg.exec()
        finally:
            self.panel.dialog_open = False
        if not accepted:
            return
        results = trash.move_to_trash([p for p, _, _ in items])
        moved = [p for p, ok, _ in results if ok]
        failed = [(p, err) for p, ok, err in results if not ok]
        self.panel.selection.mark_trashed(moved)
        self.panel.rebuild(self.state)
        tops = set(trash.top_level(moved))
        size = sum((s or 0) for p, s, _ in items if trash.canonical(p) in tops)
        text = ("Moved %d item%s (%s) to the Trash. Empty the Trash to get the space back, "
                "then Scan Now to update the numbers." % (len(tops), "" if len(tops) == 1 else "s",
                                                          fmt(size))) if moved else ""
        if failed:
            text += (" " if text else "") + "Could not move: " + "; ".join(
                "%s (%s)" % (tilde(p), err) for p, err in failed[:3])
        self.panel.status_tab.show_message(text)
        self.panel.tabs.setCurrentIndex(0)

    def _scan_failed(self, text):
        self.panel.set_error("The scan failed: " + text.strip().splitlines()[-1])

    def _scan_thread_ended(self):
        self.panel.status_tab.set_scanning(False)
        self.scan_thread = None
        self.refresh()

    def _scheduled_check(self):
        days = self.settings["schedule_days"]
        if days and not self.scanning and self.state.needs_scan(days):
            self.start_scan()

    # ------------------------------------------------------------------
    def export(self):
        if not self.state.report:
            QMessageBox.information(None, APP_NAME, "There is no scan to export yet.")
            return
        self.panel.hide()
        stamp = time.strftime("%Y-%m-%d", time.localtime(self.state.report.now))
        default = os.path.join(os.path.expanduser("~/Desktop"), "diskreport-%s.txt" % stamp)
        path, _ = QFileDialog.getSaveFileName(None, "Export Report", default, "Text (*.txt)")
        if not path:
            return
        prev_name = self.state.previous_name()
        prev = snapshots.load_snapshot(prev_name) if prev_name else None
        delta, _ = self.state.delta(prev_name) if prev else (None, "")
        text = report_text(self.state.report, prev, prev_name, delta)
        try:
            with open(path, "w") as f:   # the file the user just chose
                f.write(text)
        except OSError as e:
            QMessageBox.warning(None, APP_NAME, "Could not write %s: %s" % (path, e))

    def show_help(self):
        self.panel.hide()
        if self.help_dialog is None:
            self.help_dialog = HelpDialog()
        self.help_dialog.show()
        self.help_dialog.raise_()
        self.help_dialog.activateWindow()

    def preferences(self):
        self.panel.hide()
        dlg = PrefsDialog(self.settings)
        if dlg.exec():
            self.settings = dlg.settings
            if not settings_mod.save(self.settings):
                QMessageBox.warning(None, APP_NAME, "~/.diskreport is a symlink; "
                                    "preferences were not saved.")


def _excepthook(exc_type, exc, tb):
    """Show unexpected errors instead of vanishing from the menu bar."""
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    sys.__stderr__.write(text)
    try:
        QMessageBox.critical(None, APP_NAME, "Something went wrong:\n\n%s" % text[-1500:])
    except Exception:
        pass


def main():
    sys.excepthook = _excepthook
    qapp = QApplication(sys.argv)
    qapp.setApplicationName(APP_NAME)
    qapp.setQuitOnLastWindowClosed(False)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, APP_NAME, "The menu bar is not available.")
        return 1
    controller = DiskReportApp(qapp)
    controller.start()
    return qapp.exec()

