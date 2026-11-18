"""Snapshot files, latest-report.txt, and the delta between two snapshots.

This module writes only inside ~/.diskreport (the snapshots, plus latest-report.txt next to
them), and pruning its own old snapshots is the only removal it ever does.

SNAP_DIR is read at call time, so tests and front ends can point it elsewhere by setting
snapshots.SNAP_DIR.
"""

import bisect
import dataclasses
import datetime as dt
import json
import os
import re
import tempfile

from .scanner import HOME, THRESHOLD, under

SNAP_DIR = os.path.join(HOME, ".diskreport", "snapshots")
SNAP_KEEP = 12
SNAP_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{4}\.json$")


def list_snapshots():
    """Snapshot file names, oldest first."""
    try:
        names = os.listdir(SNAP_DIR)
    except OSError:
        return []
    return sorted(n for n in names if SNAP_NAME_RE.match(n)
                  and os.path.isfile(os.path.join(SNAP_DIR, n))
                  and not os.path.islink(os.path.join(SNAP_DIR, n)))


def load_snapshot(name):
    """The snapshot as a dict, or None if unreadable or not in this program's format."""
    try:
        with open(os.path.join(SNAP_DIR, name)) as f:
            s = json.load(f)
    except (OSError, ValueError):
        return None
    if not (isinstance(s, dict) and isinstance(s.get("epoch"), (int, float))
            and isinstance(s.get("paths"), dict)):
        return None
    df = s.get("df")
    if df is not None and not (isinstance(df, dict) and "used" in df and "available" in df):
        s["df"] = None
    return s


def previous_before(epoch, exclude=None):
    """(name, snapshot) of the newest loadable snapshot taken before `epoch`, skipping damaged
    files and `exclude` (e.g. a same-minute snapshot the new one replaced); or (None, None)."""
    for n in reversed(list_snapshots()):
        if n == exclude:
            continue
        s = load_snapshot(n)
        if s and s["epoch"] < epoch:
            return n, s
    return None, None


def latest():
    """(name, snapshot) for the newest snapshot that loads, skipping damaged ones; or None."""
    for n in reversed(list_snapshots()):
        s = load_snapshot(n)
        if s:
            return n, s
    return None


def find(prefix):
    """Names of snapshots whose name starts with prefix (e.g. '2026-11' or '2026-11-17-1542')."""
    return [n for n in list_snapshots() if n.startswith(prefix)]


def snap_dir_is_safe():
    """Refuse to write or prune if the snapshot dir (or ~/.diskreport) is a symlink to
    somewhere else, so the tool can never write or remove files outside its own folder."""
    return os.path.realpath(SNAP_DIR) == SNAP_DIR


def save_snapshot(snap, now):
    """Write the snapshot. Returns its file name, or None if the directory is unsafe."""
    if not snap_dir_is_safe():
        return None
    os.makedirs(SNAP_DIR, exist_ok=True)
    name = dt.datetime.fromtimestamp(now).strftime("%Y-%m-%d-%H%M") + ".json"
    # Write a new temp file, then rename it over the final name. A crash leaves no
    # half-written snapshot, and a symlink at the final name is replaced, not followed.
    fd, tmp = tempfile.mkstemp(prefix=".writing-", dir=SNAP_DIR)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(snap, f, indent=1)   # key order kept: check data relies on it
        os.replace(tmp, os.path.join(SNAP_DIR, name))
    except BaseException:
        os.remove(tmp)   # our own temp file
        raise
    return name


def latest_report_path():
    """~/.diskreport/latest-report.txt: the newest report as plain text, rewritten after
    every scan, so it can be read (by you, a script, or Claude) without rescanning."""
    return os.path.join(os.path.dirname(SNAP_DIR), "latest-report.txt")


def save_latest_report(text):
    """Write latest-report.txt next to the snapshot folder. Returns the path, or None if the
    folder is unsafe (a symlink to elsewhere)."""
    folder = os.path.dirname(SNAP_DIR)
    if os.path.lexists(folder) and os.path.realpath(folder) != folder:
        return None
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".writing-", dir=folder)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, latest_report_path())
    except BaseException:
        os.remove(tmp)   # our own temp file
        raise
    return latest_report_path()


def prune_snapshots():
    """Remove all but the newest SNAP_KEEP snapshot files. The only file removal in this program.
    Only touches regular files named YYYY-MM-DD-HHMM.json directly inside SNAP_DIR."""
    removed = []
    if not snap_dir_is_safe():
        return removed
    names = list_snapshots()
    for n in names[:-SNAP_KEEP] if len(names) > SNAP_KEEP else []:
        try:
            os.remove(os.path.join(SNAP_DIR, n))
        except FileNotFoundError:     # another run pruned it first
            continue
        removed.append(n)
    return removed


def snap_label(name):
    return name[:-5] if name.endswith(".json") else name


def history():
    """[(name, epoch, df dict or None)] for every snapshot that loads, oldest first."""
    out = []
    for n in list_snapshots():
        s = load_snapshot(n)
        if s:
            out.append((n, s["epoch"], s.get("df")))
    return out


# ---------------------------------------------------------------------------
# Delta
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class DeltaRow:
    change: int       # bytes, after - before
    path: str
    before: int
    after: int
    note: str         # "", " (new)" or " (gone)"


@dataclasses.dataclass
class Delta:
    prev_name: str
    prev_epoch: float
    rows: list                   # [DeltaRow], largest absolute change first
    parents_omitted: bool        # some parent folders were left out (explained by a child)
    all_rows: list               # every compared change over the threshold, parents included
    skipped: int                 # paths not compared (unmeasured in one of the two runs)
    net: object = None           # change in df "used", bytes; None if either df is missing
    days: object = None          # days between the snapshots
    weekly: object = None        # net change per week, bytes; None if under a day apart
    projection: object = None    # None (no df) | "short" | "already" | "flat" | "date"
    full_95_epoch: object = None # when projection == "date"


def compute_delta(cur, prev, prev_name, now):
    """Every measured path that changed by more than THRESHOLD between prev and cur.

    A path is compared only when it was measured in both runs, with the same parts
    unreadable beneath it; a parent folder is listed only if it changed by more than the
    threshold beyond what its listed subfolders already explain (all_rows keeps them).

    prev must be the older snapshot; a newer one raises ValueError rather than giving a
    sign-inverted result."""
    if prev["epoch"] > cur["epoch"]:
        raise ValueError("compute_delta: 'prev' (%s) is newer than the current snapshot" % prev_name)
    cur_p, old_p = cur["paths"], prev["paths"]
    cur_u = sorted(cur.get("unmeasured") or [])
    old_u = sorted(prev.get("unmeasured") or [])

    def beneath(sorted_paths, p):
        """Entries of sorted_paths equal to p or under p."""
        lo = bisect.bisect_left(sorted_paths, p + "/")
        hi = bisect.bisect_left(sorted_paths, p + "0")   # "0" sorts right after "/"
        return ([p] if p in sorted_paths else []) + sorted_paths[lo:hi]

    def unmeasured_above(sorted_paths, p):
        return any(under(p, u) for u in sorted_paths) if sorted_paths else False

    changes = []
    skipped = 0
    for p in set(cur_p) | set(old_p):
        a, b = old_p.get(p), cur_p.get(p)
        note = ""
        if b is None:
            if os.path.lexists(p):          # still there, just not measured this run
                skipped += 1
                continue
            b, note = 0, " (gone)"
        if a is None:
            if unmeasured_above(old_u, p):  # it existed before but could not be measured
                skipped += 1
                continue
            a, note = 0, " (new)"
        if abs(b - a) <= THRESHOLD:
            continue
        if beneath(old_u, p) != beneath(cur_u, p):
            skipped += 1                    # different parts unreadable: not comparable
            continue
        changes.append((b - a, p, a, b, note))

    listed = {c[1]: c for c in changes}
    keep = []
    for c in changes:
        desc = [q for q in listed if q != c[1] and under(q, c[1])]
        top = [q for q in desc if not any(r != q and under(q, r) for r in desc)]
        if top and abs(c[0] - sum(listed[q][0] for q in top)) <= THRESHOLD:
            continue
        keep.append(c)
    keep.sort(key=lambda x: -abs(x[0]))

    changes.sort(key=lambda x: -abs(x[0]))
    d = Delta(prev_name=prev_name, prev_epoch=prev["epoch"],
              rows=[DeltaRow(*c) for c in keep],
              parents_omitted=len(keep) < len(changes), skipped=skipped,
              all_rows=[DeltaRow(*c) for c in changes])

    df, pdf = cur.get("df"), prev.get("df")
    if not df or not pdf:
        return d
    d.days = (now - prev["epoch"]) / 86400
    d.net = df["used"] - pdf["used"]
    if d.days < 1:
        d.projection = "short"
        return d
    d.weekly = d.net / d.days * 7
    target = 0.95 * (df["used"] + df["available"])
    if df["used"] >= target:
        d.projection = "already"
    elif d.net <= 0:
        d.projection = "flat"
    else:
        d.projection = "date"
        d.full_95_epoch = now + (target - df["used"]) / (d.net / d.days) * 86400
    return d


# ---------------------------------------------------------------------------
# Trend across the snapshot history
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Trend:
    points: list                 # [(epoch, used bytes, available bytes)], oldest first
    weekly: object = None        # growth in used space per week (least-squares fit), bytes
    projection: object = None    # None (too little history) | "already" | "flat" | "date"
    full_95_epoch: object = None # when projection == "date"


def trend(hist, limit=SNAP_KEEP):
    """Growth rate and 95%-full projection from the last `limit` snapshots in `hist`
    (as returned by history()). Needs at least two snapshots at least a day apart."""
    points = [(e, df["used"], df["available"]) for _, e, df in hist if df][-limit:]
    t = Trend(points=points)
    if len(points) < 2 or points[-1][0] - points[0][0] < 86400:
        return t
    n = len(points)
    mean_t = sum(p[0] for p in points) / n
    mean_u = sum(p[1] for p in points) / n
    var = sum((p[0] - mean_t) ** 2 for p in points)
    slope = sum((p[0] - mean_t) * (p[1] - mean_u) for p in points) / var   # bytes per second
    t.weekly = slope * 7 * 86400
    last_e, used, avail = points[-1]
    target = 0.95 * (used + avail)
    if used >= target:
        t.projection = "already"
    elif slope <= 0:
        t.projection = "flat"
    else:
        t.projection = "date"
        t.full_95_epoch = last_e + (target - used) / slope
    return t
