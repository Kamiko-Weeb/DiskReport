"""The terminal report.

    python3 -m diskreport.cli                    scan, print report, save snapshot
    python3 -m diskreport.cli --list             list saved snapshots
    python3 -m diskreport.cli --compare 2026-11-01   diff against a specific snapshot
    python3 -m diskreport.cli --sudo             also measure root-only paths
    python3 -m diskreport.cli --last             print the newest saved report, no scan

READ-ONLY. This prints what diskreport.core measures, saves a snapshot and rewrites
~/.diskreport/latest-report.txt. It never deletes, moves, renames or modifies anything
outside ~/.diskreport, and it has no Move to Trash. The only
commands it runs itself are `sudo -v` / `sudo -k` under --sudo, which only ask for your
password and then forget it; everything else goes through core.scanner.run().
"""

import argparse
import contextlib
import datetime as dt
import io
import os
import shlex
import subprocess
import sys
import textwrap
import time

from ..core import checks, snapshots
from ..core.formatting import age_str, category, date_str, fmt, fmt_signed, shorten, tilde
from ..core.report import Report, run_report
from ..core.scanner import DATA_VOLUME, MB, THRESHOLD, TIME_BUDGET

W = 78
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def section(title):
    print()
    print("=" * W)
    print(title)
    print("=" * W)


# ---------------------------------------------------------------------------
# Header and delta
# ---------------------------------------------------------------------------

def print_header(r, prev, prev_name, now):
    df = r.df
    print("DISK REPORT  %s  %s" % (dt.datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M"),
                                   r.machine))
    if prev:
        print("Last snapshot: %s, %.1f days ago" % (snapshots.snap_label(prev_name),
                                                   (now - prev["epoch"]) / 86400))
    else:
        print("Last snapshot: none (first run)")
    if df:
        print("Data volume: %s used / %s total / %s available / %.0f%% full"
              % (fmt(df["used"]), fmt(df["size"]), fmt(df["available"]), df["capacity_pct"]))
        print("Inodes: %d used, %d free (%.0f%%)"
              % (df["inodes_used"], df["inodes_free"], df["inodes_pct"]))
        print("(Used is this volume only. The container's other volumes, such as the")
        print(" system and VM volumes, account for the rest of the total.)")
        cap = df["capacity_pct"]
        if cap > 90:
            print()
            print("WARNING: over 90% full. macOS needs free space for swap and to stage")
            print("  updates. Past this point updates fail to install and apps start")
            print("  failing in odd ways.")
        elif cap > 85:
            print()
            print("WARNING: over 85% full.")
    else:
        print("df: could not read %s" % DATA_VOLUME)
    if df:
        print()
        print("The scans below account for %s of the %s used; the rest is in folders"
              % (fmt(r.attributed), fmt(df["used"])))
        print("that could not be read, areas outside the scan list (/Library, /private,")
        print("/opt), and space held by snapshots.")
    snap_check = r.result("snapshots")
    s = snap_check.data if snap_check and snap_check.data and "pins_user_data" in snap_check.data else None
    if s and s["pins_user_data"]:
        n = max(len(s["data_volume"]), len(s["tm_local"]))
        print()
        print("APFS SNAPSHOTS PRESENT: %d snapshot(s) on the Data volume. Deleting files" % n)
        print("  may not free any space until they clear. See check 7.")
    elif s and s["update"]:
        print()
        print("APFS snapshots: %d system-volume update snapshot(s) only. These pin nothing"
              % len(s["update"]))
        print("  of yours; deleting files frees space normally. See check 7.")
    unread = len(r.m.err_paths)
    if unread:
        print()
        print("Unreadable paths skipped: %d (sizes containing them are undercounts;"
              " details in footer)" % unread)
    if r.m.timed_out:
        print("Scans cut off by the time limit: %d (sizes for them are missing)"
              % len(r.m.timed_out))


def print_delta(d):
    section("DELTA SINCE %s" % snapshots.snap_label(d.prev_name))
    if not d.rows:
        print("No measured path changed by more than %s." % fmt(THRESHOLD))
    for row in d.rows:
        print("%9s  %-46s %s -> %s%s" % (fmt_signed(row.change), shorten(tilde(row.path), 46),
                                         fmt(row.before), fmt(row.after), row.note))
    if d.parents_omitted:
        print("(Parent folders whose change is explained by a listed subfolder are omitted.)")
    if d.skipped:
        print("(%d path(s) not compared: unreadable or cut off in one of the two runs.)" % d.skipped)
    if d.projection is None:
        return
    print()
    print("Net change in used space: %s since %s"
          % (fmt_signed(d.net), snapshots.snap_label(d.prev_name)), end="")
    if d.projection == "short":
        print(" (under a day apart, no weekly rate)")
        return
    print(", %s per week" % fmt_signed(d.weekly))
    if d.projection == "already":
        print("Already at or above 95% full.")
    elif d.projection == "flat":
        print("Trend is flat or shrinking: no projection to 95% full.")
    else:
        print("At this rate, 95%% full on %s." % date_str(d.full_95_epoch))


# ---------------------------------------------------------------------------
# Targeted checks. Each check has a detailed text layout here; a check without one
# (a new check added to core) is printed by render_generic from the shared shape.
# ---------------------------------------------------------------------------

def render_staged_updates(res, now):
    u = res.data
    print("\n[1] STAGED OS UPDATES: %s on disk" % fmt(u["total"]))
    for p, v in u["dirs"].items():
        extra = ""
        if v["unreadable"]:
            extra = " (partial: %d unreadable)" % v["unreadable"]
        print("    %8s  %s%s" % (fmt(v["size"]), p, extra))
        if p == checks.UPDATE_ASSET:
            print("              (the downloaded update payload; also counted in check 9's AssetsV2 total)")
        elif p.endswith("macOS Install Data"):
            print("              (left by a macOS installer that did not finish)")
    for q in u["queued"]:
        title = q["title"] if q["version"] in q["title"] else "%s %s" % (q["title"], q["version"])
        print("    Queued: %s, %s%s" % (title, fmt(q["size"]),
                                        ", needs restart" if q["action"] == "restart" else ""))
    if u["status"] != "ok":
        print("    softwareupdate: %s" % u["status"])
    print("    Note: SIP-protected, rm cannot remove these. The fix is installing the")
    print("    update, or removing the download in System Settings > General >")
    print("    Software Update. Low free space can stop updates staging, which keeps")
    print("    the download parked here.")


def render_fusion(res, now):
    f = res.data
    print("\n[2] AUTODESK FUSION BUILDS: %d build(s), %s, %s reclaimable"
          % (len(f["builds"]), fmt(f["total"]), fmt(f["reclaimable"])))
    for b in sorted(f["builds"], key=lambda r: not r["live"]):
        label = "LIVE" if b["live"] else ("ORPHANED" if f["live"] else "UNKNOWN")
        print("    %-9s %s  %s" % (label, b["hash"], fmt(b["size"])))
    if f["link_mtime"]:
        print("    Symlink 'Autodesk Fusion.app' last changed: %s" % age_str(f["link_mtime"], now))
    if not f["link_exists"]:
        print("    No 'Autodesk Fusion.app' symlink found: cannot tell which build is live.")
    elif not f["live"]:
        print("    Symlink does not point into production/: cannot tell which build is live.")
    print("    Note: Fusion never removes old builds. Only the symlink target is in use.")
    print("    An old symlink date means Fusion has not updated, which low disk space")
    print("    can cause.")


def render_messages(res, now):
    msg = res.data
    tmp, att = msg["tmp"], msg["attachments"]
    print("\n[3] MESSAGES")
    if tmp:
        print("    Staging (Containers/com.apple.MobileSMS/Data/tmp): %s%s"
              % (fmt(tmp["size"]), " (unreadable)" if tmp["unreadable"] else ""))
        t = msg["tempitems_newest"]
        if t:
            print("    Newest item in TemporaryItems: %s" % age_str(t["mtime"], now))
    if att:
        print("    Message history (~/Library/Messages/Attachments): %s%s"
              % (fmt(att["size"]), " (unreadable)" if att["unreadable"] else ""))
    print("    Note: staging should be near empty; if its newest item is months old,")
    print("    nothing is in flight. Attachments is real message history, a different thing.")


def render_music(res, now):
    mu = res.data
    print("\n[4] PREVIOUS MUSIC LIBRARIES: %s" % fmt(mu["total"]))
    for e in mu["entries"]:
        kind = "database backup" if e["name"].endswith(".musiclibrary") else ""
        if e["name"] == "Media.localized":
            kind = "audio files"
        print("    %-40s %8s  %s" % (shorten(e["name"], 40), fmt(e["size"]), kind))
    for e in mu["media"]:
        kind = {"Apple Music": "DRM streaming downloads, re-downloadable",
                "Music": "imported/purchased files, may exist nowhere else"}.get(e["name"], "")
        print("      Media.localized/%-22s %8s  %s" % (shorten(e["name"], 22), fmt(e["size"]), kind))


def render_launchers(res, now):
    la = res.data
    print("\n[5] GAME LAUNCHERS: %d folders, %s total" % (len(la["launchers"]), fmt(la["total"])))
    for r in la["launchers"]:
        n = r["newest"]
        when = age_str(n["mtime"], now) if n else "unknown"
        if n and n["partial"]:
            when += " (walk cut short)"
        print("    %-28s %8s  newest file %s" % (shorten(os.path.basename(r["path"]), 28),
                                                  fmt(r["size"]), when))
    print("    Note: each launcher keeps its own full copy of game versions, libraries")
    print("    and assets, so much of this total is usually redundant. Which is live")
    print("    is your call; the dates are the evidence.")


def render_deps(res, now):
    de = res.data
    print("\n[6] DEPENDENCY AND BUILD FOLDERS (depth 3 under ~): %d, %s"
          % (len(de["hits"]), fmt(de["total"])))
    small = [h for h in de["hits"] if h["size"] < 10 * MB]
    for h in de["hits"]:
        if h["size"] < 10 * MB:
            continue
        pm = date_str(h["parent_mtime"]) if h["parent_mtime"] else "?"
        print("    %8s  %s" % (fmt(h["size"]), tilde(h["path"])))
        print("              project modified %s; %s" % (pm, h["evidence"]))
    if small:
        print("    plus %d smaller ones totalling %s" % (len(small), fmt(sum(h["size"] for h in small))))
    print("    Note: these are rebuildable from a lockfile or manifest where one is named.")


def render_snapshots(res, now):
    s = res.data
    if s["pins_user_data"]:
        print("\n[7] APFS SNAPSHOTS")
        print("    Data volume (pin your deleted files): %d" % len(s["data_volume"]))
        for d in s["data_volume"]:
            print("      %s (purgeable: %s)" % (d["name"], d["purgeable"]))
        extra = [t for t in s["tm_local"] if t.replace(" (dataless)", "")
                 not in {d["name"] for d in s["data_volume"]}]
        for t in extra:
            print("      %s" % t)
        print("    System volume update snapshots (pin nothing of yours): %d" % len(s["update"]))
        print("    Note: space from files deleted after a snapshot was taken stays used")
        print("    until the snapshot is removed. Time Machine local snapshots normally")
        print("    expire within 24 hours, sooner when space runs low.")
    else:
        print("\n[7] APFS SNAPSHOTS: %d system-volume update snapshot(s) only" % len(s["update"]))
        print("    Note: these live on the system volume and pin nothing of yours.")


def render_backup(res, now):
    b = res.data
    stale = b.get("days") is not None and b["days"] > 14
    print("\n[8] TIME MACHINE BACKUP")
    if b["date"]:
        print("    Last backup: %s%s" % (age_str(b["date"], now),
                                        "" if b["source"] == "tmutil latestbackup"
                                        else " (from %s)" % b["source"]))
        if stale:
            print("    WARNING: last backup is more than 14 days old.")
    if b["state"] == "not connected":
        names = ", ".join("'%s'" % n for n in b["destinations"]) or "the backup drive"
        print("    Backup drive %s is not connected, so tmutil cannot read the" % names)
        print("    latest backup date. That is not the same as having no backup.")
        if not b["date"]:
            print("    Connect it and re-run to get the date.")
    elif b["state"] == "no destination":
        print("    Time Machine has no backup destination configured.")
    elif b["state"] == "needs full disk access":
        print("    tmutil needs Full Disk Access for your terminal app to read this.")
    elif b["state"] == "unknown":
        print("    tmutil said: %s" % b["detail"])


def render_ai_models(res, now):
    ai = res.data
    print("\n[9] APPLE INTELLIGENCE / UAF MODEL ASSETS: %s of %s in AssetsV2"
          % (fmt(ai["uaf_total"]), fmt(ai["total"])))
    for r in ai["uaf"]:
        if r["size"] >= 100 * MB:
            print("    %8s  %s" % (fmt(r["size"]), os.path.basename(r["path"])))
    if ai["unreadable_uaf"]:
        print("    Partial: %d model folder(s) could not be read, so the total is a minimum:"
              % len(ai["unreadable_uaf"]))
        for n in ai["unreadable_uaf"]:
            print("      %s" % n)
        print("    (macOS privacy protection; Full Disk Access for your terminal app fixes it.)")
    print("    Note: SIP-protected. Turning Apple Intelligence off does not reliably")
    print("    release these, and there is no supported way to remove them.")
    print("    Informational only.")


def render_accounts(res, now):
    print("\n[10] OTHER USER ACCOUNTS: not yours, do not act without asking")
    for r in res.data["accounts"]:
        extra = ""
        if r["unreadable"]:
            extra = "  partial (%d unreadable, --sudo for full size)" % r["unreadable"]
        print("    %-24s %8s  owner %s%s" % (shorten(r["path"], 24), fmt(r["size"]), r["owner"], extra))
        if r.get("contains"):
            print("      archived deleted account(s): %s" % ", ".join(r["contains"]))


def render_generic(res, now):
    """Any check, from the shared result shape alone."""
    size = fmt(res.total) if res.total else ""
    print("\n[%d] %s%s" % (res.number, res.name, (": " + size) if size else ""))
    for f in res.findings:
        parts = [f.label]
        if f.size is not None:
            parts.insert(0, "%8s " % fmt(f.size))
        if f.category:
            parts.append("[%s]" % f.category)
        if f.detail:
            parts.append("- " + f.detail)
        print("    " + " ".join(parts))
    if res.note:
        print(textwrap.fill("Note: " + res.note, W, initial_indent="    ",
                            subsequent_indent="    "))


RENDERERS = {
    "staged_updates": render_staged_updates,
    "fusion": render_fusion,
    "messages": render_messages,
    "music": render_music,
    "launchers": render_launchers,
    "deps": render_deps,
    "snapshots": render_snapshots,
    "backup": render_backup,
    "ai_models": render_ai_models,
    "accounts": render_accounts,
}


def print_checks(r, now):
    quiet, cannot = [], []
    section("TARGETED CHECKS")
    for res in r.results:
        if res.state == "found":
            # A check whose data is missing (it crashed) still gets the generic layout.
            renderer = RENDERERS.get(res.key, render_generic) if res.data else render_generic
            renderer(res, now)
        elif res.state == "quiet":
            quiet.append("%d %s" % (res.number, res.quiet_label))
        if res.unavailable:
            cannot.append("%d %s" % (res.number, res.unavailable))
    print()
    if quiet:
        print("Nothing to report (under %s or current): %s" % (fmt(THRESHOLD), "; ".join(quiet)))
    for cn in cannot:
        print("Could not check: %s" % cn)


# ---------------------------------------------------------------------------
# Top directories and footer
# ---------------------------------------------------------------------------

def print_top(r, n=25):
    section("TOP %d DIRECTORIES" % n)
    print("Folders that another scan breaks down are replaced by their contents,")
    print("so nothing is counted twice.")
    print()
    print("%8s  %-13s  %-32s  %s" % ("SIZE", "CATEGORY", "FOUND BY", "PATH"))
    for p, s, label in (r.snapshot.get("top") or [])[:n]:
        print("%8s  %-13s  %-32s  %s" % (fmt(s), category(p), label, tilde(p)))
    print()
    print("CACHE = macOS rebuilds it, leave alone. REBUILDABLE = venv/node_modules/")
    print("redownloadable. SYSTEM = SIP-protected, cannot remove. USER DATA = your")
    print("files, be careful. OTHER ACCOUNT = someone else's. UNKNOWN = not sure.")


def print_footer(r, saved, removed, kept, use_sudo, elapsed):
    section("FOOTER")
    print_coverage(r, use_sudo)
    if not saved:
        print("Snapshot NOT saved: %s is a symlink; refusing to write through it."
              % tilde(snapshots.SNAP_DIR))
    else:
        print("Snapshot saved: %s" % tilde(os.path.join(snapshots.SNAP_DIR, saved)))
        print("Snapshots kept: %d of %d max%s" % (kept, snapshots.SNAP_KEEP,
              (", pruned %d old" % len(removed)) if removed else ""))
    print("Sizes: GB = 1024^3 bytes, matching du -h and df -h.")
    print("Finished in %.0f seconds." % elapsed)
    print("Run again:  cd %s && python3 -m diskreport.cli" % shlex.quote(REPO_ROOT))


def print_coverage(r, use_sudo):
    """What the scan could not read, so a truncated scan is visible."""
    m = r.m
    if m.err_paths:
        perm = sorted({e[0] for e in m.errors if "Permission denied" in e[1]})
        tcc = sorted({e[0] for e in m.errors if "not permitted" in e[1]})
        print("Unreadable paths: %d. Sizes containing them are undercounts." % len(m.err_paths))
        by_job = {}
        for p, _, job, _ in m.errors:
            by_job.setdefault(job, set()).add(p)
        worst = sorted(by_job.items(), key=lambda kv: -len(kv[1]))[:6]
        print("  Most in: " + ", ".join("%s (%d)" % (tilde(j), len(v)) for j, v in worst))
        if tcc:
            print("  %d are 'Operation not permitted' (macOS privacy protection): grant your" % len(tcc))
            print("  terminal app Full Disk Access in System Settings > Privacy & Security.")
        if perm:
            print("  %d are 'Permission denied' (owned by root or another user)." % len(perm))
            if not use_sudo:
                sp = [p for p in checks.sudo_paths() if m.unreadable_under(p)]
                if sp:
                    print("  Skipped without --sudo. With --sudo it would run:")
                    for p in sp:
                        print("    sudo du -x -k -d 0 %s" % shlex.quote(p))
    if r.no_size:
        print("Scanned folders that could not be read at all (size unknown, not in any total):")
        line = " "
        for p in r.no_size:          # wrap between paths, never inside one
            item = " " + tilde(p) + ","
            if len(line) + len(item) > W and line.strip():
                print(line)
                line = " "
            line += item
        print(line.rstrip(","))
    if m.timed_out:
        print("Cut off by the %ds time limit: %s" % (TIME_BUDGET, ", ".join(tilde(t) for t in m.timed_out)))


def report_text(r, prev=None, prev_name=None, delta=None):
    """The report as plain text, for exporting: header, delta, checks, top directories and
    what could not be read. Works for a fresh Report or one rebuilt from a snapshot."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        print_header(r, prev, prev_name, r.now)
        if delta is not None:
            print_delta(delta)
        if r.has_results:
            print_checks(r, r.now)
        else:
            section("TARGETED CHECKS")
            print("This snapshot was saved by an older version and has no check results.")
        print_top(r)
        section("FOOTER")
        print_coverage(r, use_sudo=True)
        print("Snapshot: %s" % r.snapshot.get("timestamp", ""))
        print("Sizes: GB = 1024^3 bytes, matching du -h and df -h.")
    return buf.getvalue()


def save_latest(r, saved_name=None):
    """Rewrite ~/.diskreport/latest-report.txt for this report (compared with the snapshot
    before it). Failure to write it never stops the report."""
    prev_name, prev = snapshots.previous_before(r.now, exclude=saved_name)
    delta = snapshots.compute_delta(r.snapshot, prev, prev_name, r.now) if prev else None
    try:
        return snapshots.save_latest_report(report_text(r, prev, prev_name, delta))
    except OSError:
        return None


def cmd_last():
    """Print the newest saved report without scanning."""
    found = snapshots.latest()
    if not found:
        print("No snapshots in %s. Run a scan first." % tilde(snapshots.SNAP_DIR))
        return
    name, snap = found
    r = Report.from_snapshot(snap)
    prev_name, prev = snapshots.previous_before(snap["epoch"], exclude=name)
    delta = snapshots.compute_delta(snap, prev, prev_name, r.now) if prev else None
    sys.stdout.write(report_text(r, prev, prev_name, delta))


def cmd_list():
    names = snapshots.list_snapshots()
    if not names:
        print("No snapshots in %s" % tilde(snapshots.SNAP_DIR))
        return
    print("Snapshots in %s (newest last):" % tilde(snapshots.SNAP_DIR))
    for n in names:
        s = snapshots.load_snapshot(n) or {}
        df = s.get("df") or {}
        print("  %s   used %s   %s%% full" % (snapshots.snap_label(n), fmt(df.get("used")),
                                             df.get("capacity_pct", "?")))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m diskreport.cli",
                                 description="Read-only macOS disk usage report.")
    ap.add_argument("--compare", metavar="DATE",
                    help="compare against the newest snapshot whose name starts with DATE "
                         "(YYYY-MM-DD or YYYY-MM-DD-HHMM)")
    ap.add_argument("--list", action="store_true", help="list saved snapshots and exit")
    ap.add_argument("--last", action="store_true",
                    help="print the newest saved report without scanning")
    ap.add_argument("--sudo", action="store_true",
                    help="measure root-only paths and other accounts with sudo du")
    args = ap.parse_args(argv)

    if args.list:
        cmd_list()
        return
    if args.last:
        cmd_last()
        return

    names = snapshots.list_snapshots()
    found = snapshots.latest()
    latest_name, latest = found if found else (None, None)
    cmp_name, cmp_snap = latest_name, latest
    if args.compare:
        matches = snapshots.find(args.compare)
        if not matches:
            print("No snapshot matches '%s'. Available:" % args.compare)
            for n in names:
                print("  " + snapshots.snap_label(n))
            sys.exit(1)
        cmp_name = matches[-1]
        cmp_snap = snapshots.load_snapshot(cmp_name)
        if not cmp_snap:
            print("Snapshot %s is damaged or not in this tool's format."
                  % snapshots.snap_label(cmp_name))
            sys.exit(1)

    use_sudo = False
    if args.sudo:
        print("--sudo: these read-only commands will run as root:")
        for p in checks.sudo_paths():
            if os.path.isdir(p):
                print("  sudo du -x -k -d 0 %s" % shlex.quote(p))
        print("sudo will now ask for your password.")
        sys.stdout.flush()
        if subprocess.call(["sudo", "-v"]) == 0:
            use_sudo = True
        else:
            print("sudo not granted; continuing without it.")
        print()

    start = time.time()
    try:
        r = run_report(use_sudo=use_sudo)
    finally:
        if use_sudo:
            subprocess.call(["sudo", "-k"])   # drop the cached root credential
    now = r.now

    print_header(r, latest, latest_name, now)
    if cmp_snap:
        print_delta(snapshots.compute_delta(r.snapshot, cmp_snap, cmp_name, now))
    print_checks(r, now)
    print_top(r)

    saved = snapshots.save_snapshot(r.snapshot, now)
    removed = snapshots.prune_snapshots()
    save_latest(r, saved)
    print_footer(r, saved, removed, len(snapshots.list_snapshots()), use_sudo,
                 time.time() - start)


if __name__ == "__main__":
    main()
