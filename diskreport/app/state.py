"""What the app is showing, and when to notify. No Qt here, so it can be tested on its own."""

import time

from ..core import snapshots
from ..core.formatting import fmt, tilde
from ..core.report import Report
from ..core.scanner import GB

NOTIFY_GROWTH = 5 * GB         # "something grew by more than 5 GB since the last scan"
NOTIFY_CAPACITY = 85           # "capacity crossed 85%"
NOTIFY_BACKUP_DAYS = 14        # "the backup is over 14 days old"


class AppState:
    """The report on screen (fresh or loaded from the newest snapshot) and the history."""

    def __init__(self):
        self.report = None          # core.report.Report
        self.current_name = None    # snapshot file name of `report`, if it was saved
        self.history = []           # snapshots.history()

    def load_latest(self):
        found = snapshots.latest()
        if found:
            self.current_name, snap = found
            self.report = Report.from_snapshot(snap)
        self.history = snapshots.history()

    def set_fresh(self, report, saved_name):
        self.report = report
        self.current_name = saved_name
        self.history = snapshots.history()

    # -- status ---------------------------------------------------------------
    @property
    def df(self):
        return self.report.df if self.report else None

    @property
    def capacity(self):
        return self.df["capacity_pct"] if self.df else None

    def last_scan_epoch(self):
        return self.report.now if self.report else None

    def needs_scan(self, max_age_days, now=None):
        now = time.time() if now is None else now
        last = self.last_scan_epoch()
        return last is None or now - last > max_age_days * 86400

    def trend(self):
        return snapshots.trend(self.history)

    def result(self, key):
        return self.report.result(key) if self.report else None

    def check_data(self, key):
        """A check's raw data, also for snapshots saved before check results were stored."""
        r = self.result(key)
        if r is not None:
            return r.data if r.state != "unavailable" or (r.data and "error" not in r.data) else None
        if self.report:
            return (self.report.snapshot.get("checks") or {}).get(key)
        return None

    def version_key(self):
        """Changes whenever the report or the snapshot list changes (for cheap re-render checks)."""
        return (self.current_name, self.report.now if self.report else None,
                tuple(snapshots.list_snapshots()))

    # -- changes --------------------------------------------------------------
    def compare_choices(self):
        """Older snapshots to compare against, newest first. Snapshots newer than the one on
        screen (e.g. saved by the CLI meanwhile) are left out: a delta must go forward in time."""
        now = self.report.now if self.report else float("inf")
        out = []
        for n in reversed(snapshots.list_snapshots()):
            if n == self.current_name:
                continue
            s = snapshots.load_snapshot(n)
            if s and s["epoch"] < now:
                out.append(n)
        return out

    def previous_name(self):
        """The newest snapshot older than the one on screen, if any."""
        older = [n for n in snapshots.list_snapshots()
                 if self.current_name is None or n < self.current_name]
        return older[-1] if older else None

    def delta(self, prev_name=None):
        """Delta from prev_name (default: the previous snapshot) to the report on screen.
        Returns (delta or None, message if there is no delta)."""
        if not self.report:
            return None, "No scan yet."
        prev_name = prev_name or self.previous_name()
        if not prev_name:
            return None, "Only one scan so far. Changes appear after the next scan."
        prev = snapshots.load_snapshot(prev_name)
        if not prev:
            return None, "Snapshot %s is damaged or not in this tool's format." % prev_name
        return snapshots.compute_delta(self.report.snapshot, prev, prev_name,
                                       self.report.now), ""


def _backup_days(checks_data):
    b = (checks_data or {}).get("backup") or {}
    return b.get("days")


def notify_reasons(prev_snap, report, delta):
    """Why to notify after a scan; empty list means stay quiet.

    Capacity notifies when it crosses 85%, not on every scan above it. Growth notifies for
    any path that grew more than 5 GB since the previous scan, including parent folders the
    Changes list folds into their subfolders. A backup older than 14 days notifies on every
    scan until it is fixed: that is the one worth repeating."""
    reasons = []
    cap = report.df["capacity_pct"] if report.df else None
    prev_df = (prev_snap or {}).get("df") or {}
    prev_cap = prev_df.get("capacity_pct")
    if cap is not None and cap > NOTIFY_CAPACITY and (prev_cap is None or prev_cap <= NOTIFY_CAPACITY):
        reasons.append("Disk is now %d%% full." % cap)

    if delta is not None:
        grew = sorted((r for r in delta.all_rows if r.change > NOTIFY_GROWTH),
                      key=lambda r: -r.change)
        if grew:
            top = grew[0]
            more = " and %d more" % (len(grew) - 1) if len(grew) > 1 else ""
            reasons.append("%s grew by %s since the last scan%s."
                           % (tilde(top.path), fmt(top.change), more))

    days = _backup_days(report.snapshot.get("checks"))
    if days is not None and days > NOTIFY_BACKUP_DAYS:
        reasons.append("Last Time Machine backup was %d days ago." % int(days))
    return reasons
