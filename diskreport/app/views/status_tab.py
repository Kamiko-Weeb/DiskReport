"""STATUS: free space, growth and projection, free-space sparkline, backup freshness, Scan Now."""

import time

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout,
                             QWidget)

from ...core.formatting import age_str, date_str, fmt, fmt_signed
from . import theme
from .widgets import MutedLabel, Sparkline, separator, small_font


def _stat(title):
    box = QVBoxLayout()
    box.setSpacing(0)
    value = QLabel("?")
    value.setFont(small_font(1, bold=True))
    box.addWidget(value)
    box.addWidget(MutedLabel(title, wrap=False))
    return box, value


class StatusTab(QWidget):
    scan_requested = pyqtSignal()
    show_findings_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        self.free_big = QLabel("?")
        f = QFont()
        f.setPointSize(30)
        f.setWeight(QFont.Weight.Bold)
        self.free_big.setFont(f)
        free_row = QHBoxLayout()
        free_row.addWidget(self.free_big)
        free_caption = MutedLabel("free", delta=1, wrap=False)
        free_row.addWidget(free_caption, 0, Qt.AlignmentFlag.AlignBottom)
        free_row.addStretch(1)
        lay.addLayout(free_row)

        stats = QHBoxLayout()
        stats.setSpacing(18)
        b, self.used_value = _stat("used")
        stats.addLayout(b)
        b, self.total_value = _stat("total")
        stats.addLayout(b)
        b, self.cap_value = _stat("full")
        stats.addLayout(b)
        stats.addStretch(1)
        lay.addLayout(stats)

        self.warn_label = QLabel()
        self.warn_label.setWordWrap(True)
        self.warn_label.setVisible(False)
        lay.addWidget(self.warn_label)

        self.snap_label = QLabel()
        self.snap_label.setWordWrap(True)
        self.snap_label.setVisible(False)
        lay.addWidget(self.snap_label)

        lay.addWidget(separator())

        self.last_scan = QLabel()
        lay.addWidget(self.last_scan)
        self.trend_label = QLabel()
        self.trend_label.setWordWrap(True)
        lay.addWidget(self.trend_label)

        self.spark = Sparkline()
        lay.addWidget(self.spark)
        self.spark_caption = MutedLabel("")
        lay.addWidget(self.spark_caption)

        lay.addWidget(separator())

        self.backup_label = QLabel()
        self.backup_label.setWordWrap(True)
        lay.addWidget(self.backup_label)

        lay.addStretch(1)

        self.done_box = QWidget()
        done = QHBoxLayout(self.done_box)
        done.setContentsMargins(0, 0, 0, 0)
        self.done_label = QLabel()
        self.done_label.setWordWrap(True)
        done.addWidget(self.done_label, 1)
        see = QPushButton("Show Findings")
        see.clicked.connect(self.show_findings_requested.emit)
        done.addWidget(see, 0, Qt.AlignmentFlag.AlignTop)
        self.done_box.setVisible(False)
        lay.addWidget(self.done_box)

        self.progress_label = MutedLabel("", wrap=False)
        self.progress_label.setVisible(False)
        lay.addWidget(self.progress_label)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.setVisible(False)
        lay.addWidget(self.progress)

        self.scan_button = QPushButton("Scan Now")
        self.scan_button.setDefault(True)
        self.scan_button.clicked.connect(self.scan_requested.emit)
        lay.addWidget(self.scan_button)

    # ------------------------------------------------------------------
    def update_from(self, state, now=None):
        now = time.time() if now is None else now
        df = state.df
        if df:
            self.free_big.setText(fmt(df["available"]))
            self.used_value.setText(fmt(df["used"]))
            self.total_value.setText(fmt(df["size"]))
            self.cap_value.setText("%d%%" % df["capacity_pct"])
            color = theme.capacity_color(df["capacity_pct"])
            self.cap_value.setStyleSheet("QLabel { color: %s; }" % color if color else "")
            if df["capacity_pct"] > 90:
                self._warn("Over 90% full. macOS needs free space for swap and to stage updates; "
                           "past this point updates fail to install and apps start failing.",
                           theme.CAPACITY_CRITICAL)
            elif df["capacity_pct"] > 85:
                self._warn("Over 85% full.", theme.CAPACITY_WARN)
            else:
                self.warn_label.setVisible(False)
        else:
            for w in (self.free_big, self.used_value, self.total_value, self.cap_value):
                w.setText("?")
            self.warn_label.setVisible(False)

        sdata = state.check_data("snapshots")
        if sdata and sdata.get("pins_user_data"):
            n = max(len(sdata["data_volume"]), len(sdata["tm_local"]))
            self.snap_label.setText("%d APFS snapshot(s) on the Data volume: deleting files may "
                                    "not free space until they clear." % n)
            self.snap_label.setStyleSheet("QLabel { color: %s; }" % theme.CAPACITY_WARN)
            self.snap_label.setVisible(True)
        else:
            self.snap_label.setVisible(False)

        last = state.last_scan_epoch()
        if last is None:
            self.last_scan.setText("No scan yet.")
        else:
            days = (now - last) / 86400
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(last))
            self.last_scan.setText("Last scan: %s (%s)" % (
                when, "today" if days < 1 else "%d day%s ago" % (int(days), "" if int(days) == 1 else "s")))

        t = state.trend()
        if t.weekly is None:
            self.trend_label.setText("Growth rate: needs two scans at least a day apart.")
        else:
            text = "Growth: %s per week" % fmt_signed(t.weekly)
            if t.projection == "date":
                text += ". At this rate, 95%% full on %s." % date_str(t.full_95_epoch)
            elif t.projection == "already":
                text += ". Already at or above 95% full."
            else:
                text += ". Flat or shrinking: no projection to 95% full."
            self.trend_label.setText(text)

        free_points = [(e, a) for e, _, a in t.points]
        self.spark.set_points(free_points)
        self.spark_caption.setText("Free space over the last %d scan%s" % (
            len(free_points), "" if len(free_points) == 1 else "s") if free_points else "")

        self._update_backup(state, now)

    def _warn(self, text, color):
        self.warn_label.setText(text)
        self.warn_label.setStyleSheet("QLabel { color: %s; }" % color)
        self.warn_label.setVisible(True)

    def _update_backup(self, state, now):
        b = state.check_data("backup")
        self.backup_label.setStyleSheet("")
        if not b:
            self.backup_label.setText("Time Machine: scan to check.")
            return
        if b.get("date"):
            text = "Last Time Machine backup: %s" % age_str(b["date"], now)
            if b.get("source") and b["source"] != "tmutil latestbackup":
                text += " (from %s)" % b["source"]
            if (now - b["date"]) / 86400 > 14:
                text += ". More than 14 days old."
                self.backup_label.setStyleSheet("QLabel { color: %s; }" % theme.CAPACITY_CRITICAL)
        elif b.get("state") == "not connected":
            names = ", ".join("'%s'" % n for n in b.get("destinations") or []) or "The backup drive"
            text = ("Time Machine: %s is not connected, so the last backup date cannot be read. "
                    "That is not the same as having no backup." % names)
        elif b.get("state") == "no destination":
            text = "Time Machine has no backup destination configured."
            self.backup_label.setStyleSheet("QLabel { color: %s; }" % theme.CAPACITY_CRITICAL)
        elif b.get("state") == "needs full disk access":
            text = "Time Machine: needs Full Disk Access to read the last backup date."
        else:
            text = "Time Machine: %s" % (b.get("detail") or "unknown")
        self.backup_label.setText(text)

    # ------------------------------------------------------------------
    def show_message(self, text):
        """A message under the status, e.g. 'Scan complete'. Empty text hides it."""
        self.done_label.setText(text or "")
        self.done_box.setVisible(bool(text))

    def set_scanning(self, on):
        if on:
            self.show_message("")
        self.scan_button.setEnabled(not on)
        self.scan_button.setText("Scanning…" if on else "Scan Now")
        self.progress.setVisible(on)
        self.progress_label.setVisible(on)
        if on:
            self.progress.setRange(0, 0)
            self.progress_label.setText("Starting scan…")

    def set_progress(self, stage, done, total):
        if stage == "scan" and total:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
            self.progress_label.setText("Measuring folders: %d of %d" % (done, total))
        else:
            self.progress.setRange(0, total or 0)
            self.progress.setValue(done)
            self.progress_label.setText("Running checks: %d of %d" % (done, total))
