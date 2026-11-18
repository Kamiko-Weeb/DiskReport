# DiskReport: notes for Claude

## Reading the user's disk report

When the user asks about their disk, storage, a scan, or "what's taking up space", read the
latest report yourself instead of asking them to describe it:

- `~/.diskreport/latest-report.txt`: the newest full report as plain text (header, changes
  since the previous scan, the ten targeted checks, top 25 directories, unreadable paths).
  Rewritten after every scan by the app and by the CLI.
- `python3 -m diskreport.cli --last` (from this folder): prints the same report from the
  newest snapshot, without scanning.
- `python3 -m diskreport.cli`: runs a fresh scan (20 s to 2 min), prints it, saves a snapshot.
  Use `--compare YYYY-MM-DD` to diff against an older snapshot, `--list` to see them.
- `~/.diskreport/snapshots/*.json`: the raw snapshots (newest 12). Keys: `df`, `paths`
  (every measured path to bytes), `results` (each check: name, severity, total, findings,
  note), `checks` (raw check data), `top`, `errors`, `unmeasured`.

The report marks what it could not read; sizes containing unreadable paths are
undercounts. Giving the app (or Terminal) Full Disk Access fills most gaps.

## Rules that matter

- Never delete, move or "clean up" the user's files on your own judgement. Every past
  cleanup decision needed knowledge the computer did not have (which Fusion build is live,
  which game launcher is used, "saves" vs "tmp"). Show where the space is and why; the user
  decides. The app's Move to Trash is a user action behind a confirmation dialog.
- `diskreport/core/` is standard library only, has no UI and no print statements, and
  never writes outside `~/.diskreport/`. `diskreport/cli/` is read-only.
- Every targeted check is one `@check` function in `core/checks.py` returning a
  `CheckResult` (name, severity info/notice/warning, total bytes, findings, note).
- After changing core or the CLI, the terminal output must stay identical; the
  record/replay comparison used during development compares old and new output on the
  same recorded `du`/`df` results.

## Layout

    diskreport/core/      scanner.py, checks.py, snapshots.py, formatting.py, report.py
    diskreport/cli/       python3 -m diskreport.cli
    diskreport/app/       python3 -m diskreport.app (PyQt6 menu bar app), views/, trash.py
    packaging/            build_app.sh (PyInstaller), make_icon.py, build_dmg.py
