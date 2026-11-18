"""Every external command, and the main directory scan.

READ-ONLY. Every external command goes through run(), which refuses anything not on
READ_ONLY_COMMANDS. Nothing here writes to disk.

All numbers come from du and df. Apple's Storage pane categories are never read.
"""

import concurrent.futures
import os
import platform
import subprocess
import threading
import time

HOME = os.path.realpath(os.path.expanduser("~"))
LIB = os.path.join(HOME, "Library")
APPSUP = os.path.join(LIB, "Application Support")
CONTAINERS = os.path.join(LIB, "Containers")
ASSETS = "/System/Library/AssetsV2"
DATA_VOLUME = "/System/Volumes/Data"

MB = 1024 ** 2
GB = 1024 ** 3
THRESHOLD = 500 * MB            # "changed by more than" / "found something over"
TIME_BUDGET = 110               # seconds; du jobs are cut off so a scan stays under 2 minutes
WORKERS = max(4, (os.cpu_count() or 4) + 4)

# Every external command this program may run. run() refuses anything else.
READ_ONLY_COMMANDS = (
    ("du",),
    ("df", "-k"),
    ("softwareupdate", "--list"),
    ("diskutil", "apfs", "listSnapshots"),
    ("tmutil", "latestbackup"),
    ("tmutil", "destinationinfo"),
    ("tmutil", "listlocalsnapshots"),
    ("scutil", "--get", "ComputerName"),
)


def under(path, parent):
    return path == parent or path.startswith(parent + "/")


def run(cmd, timeout=60):
    """Run a read-only command. Returns (stdout, stderr, timed_out)."""
    body = cmd[2:] if cmd[:2] == ["sudo", "-n"] else cmd
    if not any(tuple(body[:len(p)]) == p for p in READ_ONLY_COMMANDS):
        raise RuntimeError("refusing to run non-allowlisted command: %r" % (cmd,))
    if cmd[0] == "sudo" and body[0] != "du":
        raise RuntimeError("sudo is only used for du: %r" % (cmd,))
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           stdin=subprocess.DEVNULL, timeout=timeout)
        return (p.stdout.decode("utf-8", "surrogateescape"),
                p.stderr.decode("utf-8", "surrogateescape"), False)
    except subprocess.TimeoutExpired as e:
        return ((e.stdout or b"").decode("utf-8", "surrogateescape"),
                (e.stderr or b"").decode("utf-8", "surrogateescape"), True)
    except OSError as e:
        return "", str(e), False


def du(path, depth, sudo, deadline):
    """du -x -k -d DEPTH PATH. Sizes in bytes; permission errors are counted, not printed.
    The job is cut off at `deadline` (a time.time() value) so a scan has a hard time limit."""
    cmd = (["sudo", "-n"] if sudo else []) + ["du", "-x", "-k", "-d", str(depth), path]
    remaining = max(5, deadline - time.time())
    out, err, timed_out = run(cmd, timeout=remaining)
    sizes = {}
    for line in out.split("\n"):   # not splitlines(): names may contain U+2028 etc.
        kb, _, p = line.partition("\t")
        if p and kb.isdigit():
            sizes[p] = int(kb) * 1024
    errors = []
    for line in err.splitlines():
        if line.startswith("du: "):
            p, _, reason = line[4:].rpartition(": ")
            errors.append((p or line[4:], reason))
    return {"path": path, "sizes": sizes, "errors": errors,
            "timed_out": timed_out, "sudo": sudo}


def entries(d):
    """[(path, is_real_dir, lstat)] for d's children, including dotfiles. [] if missing/unreadable."""
    out = []
    try:
        with os.scandir(d) as it:
            for e in it:
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                out.append((e.path, e.is_dir(follow_symlinks=False), st))
    except OSError:
        pass
    return out


def newest_mtime(root, deadline):
    """Newest mtime of anything under root: {"mtime", "path", "partial"} or None.
    Finder's .DS_Store is skipped: browsing a dead folder in Finder rewrites it."""
    best = None
    best_path = None
    partial = False
    for dirpath, dirnames, filenames in os.walk(root):
        if time.time() > deadline:
            partial = True
            break
        for n in dirnames + filenames:
            if n == ".DS_Store":
                continue
            try:
                m = os.lstat(os.path.join(dirpath, n)).st_mtime
            except OSError:
                continue
            if best is None or m > best:
                best, best_path = m, os.path.join(dirpath, n)
    if best is None:
        return None
    return {"mtime": best, "path": best_path, "partial": partial}


def parse_df():
    """df -k for the Data volume, parsed. None if it cannot be read."""
    out, _, _ = run(["df", "-k", DATA_VOLUME])
    lines = out.strip().splitlines()
    if len(lines) < 2:
        return None
    f = lines[1].split()
    # Filesystem 1024-blocks Used Available Capacity iused ifree %iused Mounted
    try:
        total, used, avail = int(f[1]) * 1024, int(f[2]) * 1024, int(f[3]) * 1024
        cap = int(f[4].rstrip("%"))
        iused, ifree = int(f[5]), int(f[6])
    except (IndexError, ValueError):
        return None
    return {"size": total, "used": used, "available": avail,
            "capacity_pct": cap,  # as df prints it
            "inodes_used": iused, "inodes_free": ifree,
            "inodes_pct": round(100.0 * iused / (iused + ifree), 1) if iused + ifree else 0.0}


def machine_name():
    out, _, _ = run(["scutil", "--get", "ComputerName"], timeout=5)
    return out.strip() or platform.node()


# ---------------------------------------------------------------------------
# Scan plan
#
# The listed scans overlap (~/* contains ~/Library which contains
# ~/Library/Application Support), so running `du -sx` on each glob would walk
# the home folder three times. Instead, folders that another scan breaks down
# ("expanded" folders) are not measured directly: each of their children gets
# its own `du -x` job, and the parent's size is the sum. Every byte is walked
# once and the jobs run concurrently.
#
# Home children other than ~/Library get `du -d 2`, which also yields every
# folder down to depth 3 under ~ for the dependency-folder check.
# ---------------------------------------------------------------------------

EXPANDED = {HOME, LIB, APPSUP, CONTAINERS}

SCANS = [  # (label, root whose children are listed)
    ("~/Library/Application Support/*", APPSUP),
    ("~/Library/Containers/*", CONTAINERS),
    ("~/Library/*", LIB),
    ("~/*", HOME),
    ("/Applications/*", "/Applications"),
    ("~/Applications/*", os.path.join(HOME, "Applications")),
    ("/System/Library/AssetsV2/*", ASSETS),
    ("/Users/*", "/Users"),
    ("~/Desktop/*", os.path.join(HOME, "Desktop")),
    ("~/Downloads/*", os.path.join(HOME, "Downloads")),
]
SCAN_ROOTS = {root for _, root in SCANS}


def other_user_dirs():
    """Other folders in /Users. Measured as yourself by default (partial), or with
    `sudo du` when sudo is enabled."""
    return [p for p, is_dir, _ in entries("/Users") if is_dir and p != HOME]


def plan_jobs(use_sudo):
    """The main scan's du jobs: [(path, depth, sudo)]. Checks add their own on demand."""
    jobs = []

    def plan(d, depth):
        for p, is_dir, _ in entries(d):
            if not is_dir:
                continue
            if p == LIB:
                plan(p, 0)
            elif p in EXPANDED:
                plan(p, max(depth - 1, 0))
            else:
                jobs.append((p, depth, False))

    plan(HOME, 2)
    for root in ("/Applications", ASSETS):
        for p, is_dir, _ in entries(root):
            if is_dir:
                jobs.append((p, 0, False))
    for p in other_user_dirs():
        jobs.append((p, 0, use_sudo))
    return jobs


class Measurements:
    """du results merged: path -> bytes, plus which paths are undercounts."""

    def __init__(self, results):
        self.sizes = {}
        self.errors = []      # (path, reason, job_path, sudo_job)
        self.timed_out = []
        for r in results:
            for p, b in r["sizes"].items():
                self.sizes[p] = max(b, self.sizes.get(p, 0))
            for p, reason in r["errors"]:
                self.errors.append((p, reason, r["path"], r["sudo"]))
            if r["timed_out"]:
                self.timed_out.append(r["path"])
        self.err_paths = sorted({e[0] for e in self.errors})
        self._totals = {}

    def unreadable_under(self, path):
        return sum(1 for e in self.err_paths if under(e, path))

    def is_unreadable(self, path):
        return path in self.err_paths

    def timed_out_under(self, path):
        return any(under(t, path) or under(path, t) for t in self.timed_out)

    def size(self, path, is_dir=None, st=None):
        """Size of a path: summed for expanded folders, du for others, lstat for files."""
        if path in self._totals:
            return self._totals[path]
        if st is None:
            try:
                st = os.lstat(path)
            except OSError:
                return None
            is_dir = os.path.isdir(path) and not os.path.islink(path)
        if not is_dir:
            return st.st_blocks * 512
        if path in EXPANDED:
            total = st.st_blocks * 512
            for p, d, s in entries(path):
                v = self.size(p, d, s)
                total += v or 0
            self._totals[path] = total
            return total
        return self.sizes.get(path)


def scan_rows(m):
    """Every entry of every scan: [(scan label, path, size or None, is_dir)]."""
    rows = []
    for label, root in SCANS:
        for p, d, st in entries(root):
            if root == "/Users" and p != HOME and not d:
                continue
            rows.append((label, p, m.size(p, d, st), d))
    return rows


def top_rows(rows):
    """Deduplicated (path, size, scan label) rows, biggest first. A folder that another scan
    breaks down is replaced by its contents, so the rows never overlap."""
    seen = {}
    for label, p, s, is_dir in rows:
        if s is None or p in SCAN_ROOTS or p == HOME or p in seen:
            continue
        seen[p] = (s, label)
    return sorted(((p, s, label) for p, (s, label) in seen.items()), key=lambda r: -r[1])


class ScanContext:
    """What a targeted check gets to measure with.

    A check runs as soon as the scan starts, in its own thread. It can run commands and du
    jobs of its own (they run concurrently with the main scan), and it can read the main
    scan's numbers through `ctx.m`, which waits until the main scan has finished. So a new
    check needs no changes anywhere else to get the measurements it wants."""

    def __init__(self, use_sudo, now, deadline, workers=WORKERS):
        self.use_sudo = use_sudo
        self.now = now
        self.deadline = deadline
        self._main = None
        self._main_ready = threading.Event()
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
        self._lock = threading.Lock()
        self._extra = []          # (check number, path, du result)
        self._local = threading.local()

    # -- set by the runner --------------------------------------------------
    def set_main(self, measurements):
        self._main = measurements
        self._main_ready.set()

    def set_current_check(self, number):
        self._local.number = number

    def close(self):
        self._pool.shutdown(wait=True)

    def extra_results(self):
        """du results the checks asked for, in a stable order (by check, then path)."""
        with self._lock:
            return [r for _, _, r in sorted(self._extra, key=lambda x: (x[0], x[1]))]

    # -- for checks ---------------------------------------------------------
    @property
    def m(self):
        """The main scan's Measurements. Blocks until the main scan has finished."""
        self._main_ready.wait()
        return self._main

    def run(self, cmd, timeout=60):
        return run(cmd, timeout)

    def du(self, path, depth=0, sudo=False):
        return self._du(path, depth, sudo, getattr(self._local, "number", 999))

    def du_many(self, paths, depth=0, sudo=False):
        number = getattr(self._local, "number", 999)
        return list(self._pool.map(lambda p: self._du(p, depth, sudo, number), paths))

    def newest_mtime(self, path):
        return newest_mtime(path, self.deadline)

    def newest_mtime_many(self, paths):
        return dict(zip(paths, self._pool.map(self.newest_mtime, paths)))

    def _du(self, path, depth, sudo, number):
        r = du(path, depth, sudo, self.deadline)
        with self._lock:
            self._extra.append((number, path, r))
        return r
