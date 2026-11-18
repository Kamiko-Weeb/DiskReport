"""The diskreport engine. Standard library only, no UI, no printing: it returns data.

scanner.py      every external command (du, df, diskutil, tmutil, softwareupdate) and the
                main directory scan
checks.py       the targeted checks, each returning a CheckResult of the same shape
snapshots.py    snapshot files and latest-report.txt (the only files core writes, all in
                ~/.diskreport; it removes only its own old snapshots) and the delta
formatting.py   bytes to text, dates, category labels
report.py       runs a full scan and checks and assembles the snapshot

Typical use:

    from diskreport.core import report, snapshots
    r = report.run_report()
    prev = snapshots.latest()
    delta = snapshots.compute_delta(r.snapshot, prev[1], r.now) if prev else None
"""
