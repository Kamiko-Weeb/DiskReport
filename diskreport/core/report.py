"""Run a full scan plus every targeted check, and assemble the snapshot.

The main du scan and the checks run at the same time: each check starts immediately in its
own thread and waits for the main scan's numbers only when it needs them.
"""

import concurrent.futures
import dataclasses
import datetime as dt
import time

from . import checks as checks_mod
from .scanner import (ASSETS, EXPANDED, HOME, SCAN_ROOTS, TIME_BUDGET, WORKERS, Measurements,
                      ScanContext, du, machine_name, parse_df, plan_jobs, scan_rows, top_rows,
                      under)

SNAPSHOT_VERSION = 2


@dataclasses.dataclass
class Report:
    start: float           # time.time() when the scan started
    now: float             # the scan's timestamp
    machine: str
    df: object             # parsed df dict, or None
    m: Measurements        # every du result, main scan and checks
    scans: list            # [(scan label, path, size or None, is_dir)]
    results: list          # [CheckResult], in check-number order
    no_size: list          # scanned folders that could not be read at all
    attributed: int        # bytes the (non-overlapping) scan rows account for
    snapshot: dict         # what gets saved

    def result(self, key):
        for r in self.results:
            if r.key == key:
                return r
        return None

    @classmethod
    def from_snapshot(cls, snap):
        """Rebuild a Report from a saved snapshot, so a front end can show it without
        rescanning. Snapshots from before version 2 have no check results; `results` is
        then empty and `has_results` False."""
        by_job = {}
        for e in snap.get("errors") or []:
            path, reason, job = e[0], e[1], e[2]
            sudo = bool(e[3]) if len(e) > 3 else False
            by_job.setdefault((job, sudo), []).append((path, reason))
        fake = [{"path": job, "sizes": {}, "errors": errs, "timed_out": False, "sudo": sudo}
                for (job, sudo), errs in by_job.items()]
        fake += [{"path": t, "sizes": {}, "errors": [], "timed_out": True, "sudo": False}
                 for t in snap.get("timed_out") or []]
        checks_data = snap.get("checks") or {}
        results = [checks_mod.CheckResult.from_dict(d, data=checks_data.get(d.get("key")))
                   for d in snap.get("results") or []]
        epoch = snap["epoch"]
        return cls(start=epoch, now=epoch, machine=snap.get("machine") or "", df=snap.get("df"),
                   m=Measurements(fake), scans=[], results=results,
                   no_size=list(snap.get("no_size") or []),
                   attributed=snap.get("attributed") or 0, snapshot=snap)

    @property
    def has_results(self):
        return bool(self.results)


def run_report(use_sudo=False, now=None, progress=None):
    """Scan, run every check, and return a Report. Nothing is saved; see snapshots.save_snapshot.

    progress, if given, is called as progress(stage, done, total) with stage "scan" while du
    jobs finish and "checks" while checks finish. It is called from worker threads."""
    start = time.time()
    if now is None:
        now = time.time()
    deadline = start + TIME_BUDGET

    def report_progress(stage, done, total):
        if progress is not None:
            progress(stage, done, total)

    jobs = plan_jobs(use_sudo)
    ctx = ScanContext(use_sudo, now, deadline)
    all_checks = list(checks_mod.CHECKS)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as du_pool, \
                concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(all_checks))) as ck_pool:
            df_f = du_pool.submit(parse_df)
            machine_f = du_pool.submit(machine_name)
            check_futs = [ck_pool.submit(checks_mod.run_check, c, ctx) for c in all_checks]
            du_futs = [du_pool.submit(du, p, depth, s, deadline) for p, depth, s in jobs]
            try:
                done = 0
                report_progress("scan", 0, len(du_futs))
                for _ in concurrent.futures.as_completed(du_futs):
                    done += 1
                    report_progress("scan", done, len(du_futs))
                main_results = [f.result() for f in du_futs]
            except BaseException:
                # Interrupted (Ctrl+C, a failing progress callback): drop queued du jobs and
                # release checks waiting on ctx.m, so the pools can shut down instead of hanging.
                for f in du_futs:
                    f.cancel()
                ctx.set_main(Measurements([]))
                raise
            ctx.set_main(Measurements(main_results))
            done = 0
            report_progress("checks", 0, len(check_futs))
            for _ in concurrent.futures.as_completed(check_futs):
                done += 1
                report_progress("checks", done, len(check_futs))
            results = [f.result() for f in check_futs]
            df = df_f.result()
            machine = machine_f.result()
    finally:
        ctx.close()

    m = Measurements(main_results + ctx.extra_results())
    scans = scan_rows(m)

    # Every measured path, for the snapshot and the delta.
    paths = {}
    for _, p, s, _ in scans:
        if s is not None:
            paths[p] = s
    for p in list(EXPANDED) + list(SCAN_ROOTS):
        s = m.size(p)
        if s is not None:
            paths[p] = s
    for p, s in m.sizes.items():
        if not under(p, HOME) or p.count("/") - HOME.count("/") <= 3:
            paths.setdefault(p, s)
    assets_total = sum(s or 0 for label, p, s, _ in scans if label == "/System/Library/AssetsV2/*")
    if assets_total:
        paths[ASSETS] = assets_total

    # Paths whose size is missing or an undercount this run. The delta only compares a
    # path when the same things were unmeasured beneath it in both runs.
    no_size = sorted(p for _, p, s, d in scans if s is None and d)
    unmeasured = sorted(set(m.err_paths) | set(m.timed_out) | set(no_size))

    # The top-directory rows are disjoint, so their sum is how much of "used" the
    # scans can attribute.
    top = top_rows(scans)
    attributed = sum(s for _, s, _ in top)

    snapshot = {
        "version": SNAPSHOT_VERSION,
        "timestamp": dt.datetime.fromtimestamp(now).isoformat(timespec="seconds"),
        "epoch": now,
        "machine": machine,
        "df": df,
        "paths": paths,
        "unreadable_paths": len(m.err_paths),
        "unmeasured": unmeasured,
        "checks": {r.key: r.data for r in results},
        # Added in version 2, for front ends that show a saved snapshot without rescanning:
        "results": [r.to_dict() for r in results],
        "top": [[p, s, label] for p, s, label in top],
        "attributed": attributed,
        "no_size": no_size,
        "timed_out": list(m.timed_out),
        "errors": [[p, reason, job, sudo] for p, reason, job, sudo in m.errors],
        "duration": time.time() - start,
    }
    return Report(start=start, now=now, machine=machine, df=df, m=m, scans=scans,
                  results=results, no_size=no_size, attributed=attributed, snapshot=snapshot)
