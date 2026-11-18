"""The targeted checks.

Each check is one function decorated with @check. It receives a ScanContext and returns a
CheckResult. Every result has the same shape:

    name        "STAGED OS UPDATES"
    severity    "info" | "notice" | "warning"
    total       bytes (0 when sizes do not apply, e.g. backups)
    findings    [Finding(label, size, path, detail, category, mtime)]
    note        one plain-English sentence on what it means
    state       "found"        something over the threshold or time-sensitive: show it
                "quiet"        checked, nothing to report
                "unavailable"  could not be checked (see `unavailable`)
                "absent"       the thing it looks for does not exist on this Mac
    data        the check's raw numbers, kept in snapshots and used by the CLI's
                detailed text layout

A front end can render any check from the generic fields alone, so adding check eleven
means writing one more decorated function here and nothing else.

READ-ONLY. Checks only measure. None of them, and nothing they call, deletes or moves files.
"""

import dataclasses
import datetime as dt
import json
import os
import plistlib
import pwd
import re
import traceback

from .formatting import age_str, category, date_str, tilde
from .scanner import (APPSUP, ASSETS, CONTAINERS, HOME, LIB, THRESHOLD, Measurements,
                      entries, other_user_dirs, under)

SEVERITIES = ("info", "notice", "warning")


@dataclasses.dataclass
class Finding:
    label: str
    size: object = None        # bytes, or None when unknown / not applicable
    path: object = None        # a path the user can reveal in Finder, or None
    detail: str = ""
    category: object = None    # one of formatting.CATEGORIES, or None for non-disk items
    mtime: object = None       # a relevant date (epoch seconds), or None

    def to_dict(self):
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d):
        f = cls(label=d.get("label") or "")
        for name in ("size", "path", "detail", "category", "mtime"):
            if name in d:
                setattr(f, name, d[name])
        return f


@dataclasses.dataclass
class CheckResult:
    severity: str = "info"
    total: int = 0
    findings: list = dataclasses.field(default_factory=list)
    note: str = ""
    state: str = "quiet"
    quiet_label: str = ""          # short text for "nothing to report" lists
    unavailable: object = None     # why it could not be (fully) checked, or None
    data: object = None
    # Filled in by the @check decorator:
    number: int = 0
    key: str = ""
    name: str = ""

    def to_dict(self, with_data=False):
        d = {k: getattr(self, k) for k in ("number", "key", "name", "severity", "total",
                                            "note", "state", "quiet_label", "unavailable")}
        d["findings"] = [f.to_dict() for f in self.findings]
        if with_data:
            d["data"] = self.data
        return d

    @classmethod
    def from_dict(cls, d, data=None):
        r = cls(**{k: d.get(k) for k in ("number", "key", "name", "severity", "total",
                                         "note", "state", "quiet_label", "unavailable")})
        r.findings = [Finding.from_dict(f) for f in d.get("findings") or []]
        r.data = d.get("data", data)
        return r


@dataclasses.dataclass
class Check:
    number: int
    key: str
    name: str
    fn: object


CHECKS = []


def check(number, key, name):
    """Register a targeted check. The function takes a ScanContext, returns a CheckResult."""
    def deco(fn):
        CHECKS.append(Check(number, key, name, fn))
        CHECKS.sort(key=lambda c: c.number)
        return fn
    return deco


def run_check(c, ctx):
    """Run one check. A check that crashes becomes an 'unavailable' result instead of
    taking the whole scan down."""
    ctx.set_current_check(c.number)
    try:
        r = c.fn(ctx)
        if not isinstance(r, CheckResult):
            raise TypeError("returned %r instead of a CheckResult" % type(r).__name__)
        json.dumps(r.to_dict(with_data=True))   # it must be storable in a snapshot
    except Exception as e:     # one broken check must not stop the report
        r = CheckResult(state="unavailable",
                        unavailable="%s failed: %s" % (c.name.lower(), e),
                        data={"error": traceback.format_exc()})
    r.number, r.key, r.name = c.number, c.key, c.name
    if r.severity not in SEVERITIES:
        r.severity = "info"
    return r


def _dir_or_file_size(m, p, is_dir, st):
    return m.sizes.get(p) if is_dir else st.st_blocks * 512


# ---------------------------------------------------------------------------
# Why each check exists
#
# 1. STAGED OS UPDATES. /System/Volumes/Update and /Library/Updates hold macOS
#    updates that were downloaded but not installed. Last time this was 15.8 GB,
#    and the Mac had been failing to stage a major update for months because it
#    had no free space, so the problem fed itself. These are SIP-protected: the
#    fix is installing the update or removing it in System Settings, never rm.
#    "macOS Install Data" is left behind by an installer that never finished, and
#    the downloaded payload itself sits in AssetsV2 (Update/ only symlinks to it).
#
# 2. AUTODESK FUSION ORPHANED BUILDS. Fusion downloads each new version into a
#    hash-named folder (8-9 GB) under webdeploy/production and never removes
#    the old ones. The "Autodesk Fusion.app" symlink there points at the live
#    build; every other build is an orphan. An old symlink mtime means Fusion
#    has not updated, which can itself be a symptom of low disk space.
#
# 3. MESSAGES STAGING. The Messages container's tmp folder should be near
#    empty; it was 8.4 GB. The newest mtime in TemporaryItems says whether
#    anything is still in flight. ~/Library/Messages/Attachments is real
#    message history and is reported separately so the two never get confused.
#
# 4. ORPHANED MUSIC LIBRARY. "Previous Libraries" is left behind by library
#    migrations. The .musiclibrary entries are small database backups;
#    Media.localized holds audio. Inside it, "Apple Music" is DRM streaming
#    downloads (re-downloadable) and "Music" is imported/purchased files that
#    may exist nowhere else.
#
# 5. DUPLICATE GAME LAUNCHERS. Every Minecraft launcher keeps its own full copy
#    of game versions, libraries and assets. Which one is live is only knowable
#    from use, so the check shows sizes and newest mtimes and makes no verdict.
#    Some launchers (Lunar) keep their game files in a dot-folder in ~.
#
# 6. VIRTUAL ENVIRONMENTS AND DEPENDENCY FOLDERS. node_modules, venvs, build
#    output and similar folders are rebuildable from a lockfile or manifest.
#    Generic names like "build" or "env" can also be real data, so each hit is
#    checked for a manifest beside it and labelled unverified if there is none.
#
# 7. APFS SNAPSHOTS. A snapshot pins the blocks of files deleted after it was
#    taken, so deleting things frees nothing until it clears. Update snapshots
#    live on the system volume and pin nothing of yours; Time Machine local
#    snapshots live on the Data volume and do.
#
# 8. BACKUP FRESHNESS. The backup was believed current and was five and a half
#    months old. An unmounted destination is reported as "drive not connected",
#    not as "no backup".
#
# 9. APPLE INTELLIGENCE MODELS. Large UAF_ asset folders in AssetsV2. They are
#    SIP-protected, turning the feature off does not reliably release them,
#    and there is no supported removal path. Informational only.
#
# 10. OTHER USER ACCOUNTS. Other people's home folders count against the same
#    disk. They are listed so the space is accounted for, never to be acted on
#    without asking their owner.
# ---------------------------------------------------------------------------

# Root-owned paths for check 1: measured as yourself by default (partial), or with
# `sudo du` when sudo is enabled.
UPDATE_PATHS = ["/System/Volumes/Update", "/Library/Updates",
                # Left behind by a macOS installer that never finished; under no scan root.
                "/System/Volumes/Data/macOS Install Data"]
# The downloaded update payload itself lives here (Update/ only symlinks to it).
# Shown in check 1 for clarity; it is also part of the AssetsV2 scan and check 9's total.
UPDATE_ASSET = os.path.join(ASSETS, "com_apple_MobileAsset_MacSoftwareUpdate")

FUSION_PROD = os.path.join(APPSUP, "Autodesk", "webdeploy", "production")
FUSION_LINK = os.path.join(FUSION_PROD, "Autodesk Fusion.app")
MESSAGES_TMP = os.path.join(CONTAINERS, "com.apple.MobileSMS", "Data", "tmp")
MESSAGES_TEMPITEMS = os.path.join(MESSAGES_TMP, "TemporaryItems")
MESSAGES_ATTACH = os.path.join(LIB, "Messages", "Attachments")
MUSIC_PREV = os.path.join(HOME, "Music", "Music", "Previous Libraries.localized")
LAUNCHER_WORDS = ("minecraft", "modrinth", "curseforge", "lunar", "prism",
                  "atlauncher", "technic", "badlion", "feather")
DEP_NAMES = {"node_modules", "venv", ".venv", "env", "__pycache__", ".next",
             "target", "build", "dist", "DerivedData", "Pods", ".gradle"}
MANIFESTS = ("package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
             "requirements.txt", "pyproject.toml", "Pipfile", "Pipfile.lock",
             "poetry.lock", "uv.lock", "setup.py", "setup.cfg", "Cargo.toml",
             "Cargo.lock", "Podfile", "Podfile.lock", "build.gradle",
             "build.gradle.kts", "settings.gradle", "settings.gradle.kts",
             "pom.xml", "Gemfile", "go.mod", "CMakeLists.txt", "Makefile",
             "Package.swift", "next.config.js", "next.config.mjs", "next.config.ts")


def sudo_paths():
    """Every path that `sudo du` would measure when sudo is enabled."""
    return UPDATE_PATHS + other_user_dirs()


# ---------------------------------------------------------------------------
# 1. Staged OS updates
# ---------------------------------------------------------------------------

@check(1, "staged_updates", "STAGED OS UPDATES")
def staged_updates(ctx):
    out, err, timed_out = ctx.run(["softwareupdate", "--list"], 90)
    queued = []
    for mt in re.finditer(r"Title: (.*?), Version: (.*?), Size: (\d+)K(?:iB)?,(.*)", out):
        action = re.search(r"Action: (\w+)", mt.group(4))
        queued.append({"title": mt.group(1), "version": mt.group(2),
                       "size": int(mt.group(3)) * 1024,
                       "action": action.group(1) if action else ""})
    present = [p for p in UPDATE_PATHS if os.path.isdir(p)]
    mm = Measurements(ctx.du_many(present, sudo=ctx.use_sudo))
    dirs = {}
    for p in present:
        dirs[p] = {"size": mm.sizes.get(p), "unreadable": mm.unreadable_under(p)}
    if os.path.isdir(UPDATE_ASSET):
        m = ctx.m
        dirs[UPDATE_ASSET] = {"size": m.sizes.get(UPDATE_ASSET),
                              "unreadable": m.unreadable_under(UPDATE_ASSET)}
    total = sum(v["size"] or 0 for v in dirs.values())
    status = "ok"
    if timed_out:
        status = "softwareupdate timed out"
    elif not out and err:
        status = "softwareupdate failed: " + err.strip().splitlines()[-1]
    data = {"dirs": dirs, "total": total, "queued": queued, "status": status}

    findings = []
    for p, v in dirs.items():
        detail = ""
        if p == UPDATE_ASSET:
            detail = "the downloaded update payload; also counted in check 9's AssetsV2 total"
        elif p.endswith("macOS Install Data"):
            detail = "left by a macOS installer that did not finish"
        if v["unreadable"]:
            detail = ("partial: %d unreadable" % v["unreadable"]) + ("; " + detail if detail else "")
        findings.append(Finding(label=p, size=v["size"], path=p, detail=detail,
                                category=category(p)))
    for q in queued:
        title = q["title"] if q["version"] in q["title"] else "%s %s" % (q["title"], q["version"])
        findings.append(Finding(label="Queued: " + title, size=q["size"], category="SYSTEM",
                                detail="needs restart" if q["action"] == "restart" else ""))
    if status != "ok":
        findings.append(Finding(label="softwareupdate", detail=status, category="UNKNOWN"))

    shown = total > THRESHOLD or bool(queued)
    r = CheckResult(
        severity="notice" if shown else "info", total=total, findings=findings,
        note="SIP-protected, so rm cannot remove these. The fix is installing the update, "
             "or removing the download in System Settings > General > Software Update.",
        state="found" if shown else ("unavailable" if status != "ok" else "quiet"),
        quiet_label="staged updates", data=data)
    if not shown and status != "ok":
        r.unavailable = "queued updates (%s)" % status
    return r


# ---------------------------------------------------------------------------
# 2. Autodesk Fusion orphaned builds
# ---------------------------------------------------------------------------

@check(2, "fusion", "AUTODESK FUSION BUILDS")
def fusion(ctx):
    if not os.path.isdir(FUSION_PROD):
        return CheckResult(state="absent", data=None)
    builds = [p for p, d, _ in entries(FUSION_PROD) if d]
    mm = Measurements(ctx.du_many(builds))
    live = None
    link_mtime = None
    if os.path.islink(FUSION_LINK):
        target = os.path.normpath(os.path.join(FUSION_PROD, os.readlink(FUSION_LINK)))
        rel = os.path.relpath(target, FUSION_PROD)
        if not rel.startswith(".."):
            live = os.path.join(FUSION_PROD, rel.split(os.sep)[0])
        link_mtime = os.lstat(FUSION_LINK).st_mtime
    rows = [{"path": b, "hash": os.path.basename(b), "size": mm.sizes.get(b),
             "live": b == live} for b in builds]
    orphans = [r for r in rows if not r["live"]] if live else []
    data = {"builds": rows, "live": live, "link_exists": os.path.islink(FUSION_LINK),
            "link_mtime": link_mtime,
            "reclaimable": sum(r["size"] or 0 for r in orphans),
            "total": sum(r["size"] or 0 for r in rows)}

    findings = []
    for b in sorted(rows, key=lambda r: not r["live"]):
        status = "LIVE" if b["live"] else ("ORPHANED" if live else "UNKNOWN")
        findings.append(Finding(label="%s %s" % (status, b["hash"]), size=b["size"],
                                path=b["path"], category=category(b["path"])))
    if link_mtime:
        findings.append(Finding(label="Symlink 'Autodesk Fusion.app' last changed",
                                path=FUSION_LINK, mtime=link_mtime, category=category(FUSION_LINK),
                                detail=age_str(link_mtime, ctx.now)))
    if not data["link_exists"]:
        findings.append(Finding(label="No 'Autodesk Fusion.app' symlink found",
                                detail="cannot tell which build is live", category="UNKNOWN"))
    elif not live:
        findings.append(Finding(label="Symlink does not point into production/",
                                detail="cannot tell which build is live", category="UNKNOWN"))

    shown = data["total"] > THRESHOLD
    unsure = bool(rows) and not live
    return CheckResult(
        severity="notice" if shown and (data["reclaimable"] > THRESHOLD or unsure) else "info",
        total=data["total"], findings=findings,
        note="Fusion never removes old builds; only the one the 'Autodesk Fusion.app' "
             "symlink points at is in use. An old symlink date means Fusion has not updated.",
        state="found" if shown else "quiet", quiet_label="Fusion builds", data=data)


# ---------------------------------------------------------------------------
# 3. Messages staging vs message history
# ---------------------------------------------------------------------------

@check(3, "messages", "MESSAGES")
def messages(ctx):
    present = [p for p in (MESSAGES_TMP, MESSAGES_ATTACH) if os.path.lexists(p)]
    mm = Measurements(ctx.du_many(present))
    temp_mtime = ctx.newest_mtime(MESSAGES_TEMPITEMS)

    def one(p):
        if p not in present:
            return None
        return {"size": mm.sizes.get(p), "unreadable": mm.is_unreadable(p)}

    tmp, att = one(MESSAGES_TMP), one(MESSAGES_ATTACH)
    data = {"tmp": tmp, "tempitems_newest": temp_mtime, "attachments": att}

    findings = []
    if tmp:
        findings.append(Finding(label="Staging (Containers/com.apple.MobileSMS/Data/tmp)",
                                size=tmp["size"], path=MESSAGES_TMP,
                                detail="unreadable" if tmp["unreadable"] else "",
                                category=category(MESSAGES_TMP)))
        if temp_mtime:
            findings.append(Finding(label="Newest item in TemporaryItems",
                                    path=temp_mtime["path"], mtime=temp_mtime["mtime"],
                                    category=category(temp_mtime["path"]),
                                    detail=age_str(temp_mtime["mtime"], ctx.now)))
    if att:
        findings.append(Finding(label="Message history (~/Library/Messages/Attachments)",
                                size=att["size"], path=MESSAGES_ATTACH,
                                detail="unreadable" if att["unreadable"] else "",
                                category=category(MESSAGES_ATTACH)))

    unreadable = bool((tmp and tmp["unreadable"]) or (att and att["unreadable"]))
    big_tmp = bool(tmp and (tmp["size"] or 0) > THRESHOLD)
    shown = big_tmp or bool(att and (att["size"] or 0) > THRESHOLD)
    state = "found" if shown else ("unavailable" if unreadable else "quiet")
    return CheckResult(
        severity="warning" if big_tmp else "info",
        total=((tmp or {}).get("size") or 0) + ((att or {}).get("size") or 0),
        findings=findings,
        note="Staging should be near empty; if its newest item is months old, nothing is in "
             "flight. Attachments is real message history, a different thing.",
        state=state, quiet_label="Messages",
        unavailable=("Messages folders (macOS privacy protection; give your terminal app "
                     "Full Disk Access)") if unreadable else None,
        data=data)


# ---------------------------------------------------------------------------
# 4. Orphaned Music library
# ---------------------------------------------------------------------------

@check(4, "music", "PREVIOUS MUSIC LIBRARIES")
def music(ctx):
    if not os.path.isdir(MUSIC_PREV):
        return CheckResult(state="absent", data=None)
    mm = Measurements([ctx.du(MUSIC_PREV, 2)])
    rows = []
    media_rows = []
    for p, d, st in entries(MUSIC_PREV):
        rows.append({"name": os.path.basename(p), "size": _dir_or_file_size(mm, p, d, st)})
        if os.path.basename(p) == "Media.localized" and d:
            for q, qd, qst in entries(p):
                media_rows.append({"name": os.path.basename(q),
                                   "size": _dir_or_file_size(mm, q, qd, qst)})
    data = {"total": mm.sizes.get(MUSIC_PREV), "entries": rows, "media": media_rows}

    findings = []
    for e in rows:
        kind = "database backup" if e["name"].endswith(".musiclibrary") else ""
        if e["name"] == "Media.localized":
            kind = "audio files"
        p = os.path.join(MUSIC_PREV, e["name"])
        findings.append(Finding(label=e["name"], size=e["size"], path=p, detail=kind,
                                category=category(p)))
    media_kinds = {"Apple Music": "DRM streaming downloads, re-downloadable",
                   "Music": "imported/purchased files, may exist nowhere else"}
    for e in media_rows:
        p = os.path.join(MUSIC_PREV, "Media.localized", e["name"])
        findings.append(Finding(label="Media.localized/" + e["name"], size=e["size"], path=p,
                                detail=media_kinds.get(e["name"], ""), category=category(p)))

    shown = (data["total"] or 0) > THRESHOLD
    return CheckResult(
        severity="notice" if shown else "info", total=data["total"] or 0, findings=findings,
        note="Left behind by Music library migrations. 'Apple Music' is re-downloadable; "
             "'Music' holds imported or purchased files that may exist nowhere else.",
        state="found" if shown else "quiet", quiet_label="previous Music libraries",
        data=data)


# ---------------------------------------------------------------------------
# 5. Duplicate game launchers
# ---------------------------------------------------------------------------

def launcher_dirs():
    """Matching folders in Application Support, plus matching dot-folders directly in ~
    (some launchers, e.g. Lunar, keep their game files in ~/.lunarclient)."""
    out = []
    for root, dot_only in ((APPSUP, False), (HOME, True)):
        for p, d, st in entries(root):
            name = os.path.basename(p)
            if d and (not dot_only or name.startswith(".")) \
                    and any(w in name.lower() for w in LAUNCHER_WORDS):
                out.append((p, d, st))
    return out


@check(5, "launchers", "GAME LAUNCHERS")
def launchers(ctx):
    dirs = launcher_dirs()
    mtimes = ctx.newest_mtime_many([p for p, _, _ in dirs])
    m = ctx.m
    rows = []
    for p, d, st in dirs:
        rows.append({"path": p, "size": m.size(p, d, st), "newest": mtimes.get(p)})
    rows.sort(key=lambda r: -(r["size"] or 0))
    data = {"launchers": rows, "total": sum(r["size"] or 0 for r in rows)}

    findings = []
    for r in rows:
        n = r["newest"]
        detail = ("newest file " + age_str(n["mtime"], ctx.now)) if n else "newest file unknown"
        if n and n["partial"]:
            detail += " (walk cut short)"
        findings.append(Finding(label=os.path.basename(r["path"]), size=r["size"],
                                path=r["path"], detail=detail, category=category(r["path"]),
                                mtime=n["mtime"] if n else None))

    shown = bool(rows) and data["total"] > THRESHOLD
    return CheckResult(
        severity="notice" if shown and len(rows) > 1 else "info", total=data["total"],
        findings=findings,
        note="Each launcher keeps its own full copy of game versions, libraries and assets, "
             "so much of this is usually redundant. Which one is live is your call; the "
             "dates are the evidence.",
        state="found" if shown else "quiet", quiet_label="game launchers", data=data)


# ---------------------------------------------------------------------------
# 6. Virtual environments and dependency folders
# ---------------------------------------------------------------------------

def dep_evidence(path):
    name = os.path.basename(path)
    parent = os.path.dirname(path)
    if name == "__pycache__":
        return "rebuildable: Python regenerates it"
    try:
        siblings = os.listdir(parent)
    except OSError:
        siblings = []
    found = [s for s in MANIFESTS if s in siblings]
    found += [s for s in siblings if s.endswith((".xcodeproj", ".xcworkspace"))]
    if found:
        return "rebuildable from " + ", ".join(sorted(found)[:3])
    if name in ("venv", ".venv", "env") and os.path.exists(os.path.join(path, "pyvenv.cfg")):
        return "a Python venv, but no requirements/lock file beside it: package list may be lost"
    return "UNVERIFIED: name match only, no project manifest beside it"


@check(6, "deps", "DEPENDENCY AND BUILD FOLDERS")
def deps(ctx):
    m = ctx.m
    hits = []
    for p in sorted(m.sizes):
        if not under(p, HOME) or under(p, LIB) or under(p, os.path.join(HOME, ".Trash")):
            continue
        rel = p[len(HOME) + 1:]
        if not rel or rel.count("/") > 2:      # depth 1..3 below ~
            continue
        if os.path.basename(p) not in DEP_NAMES:
            continue
        if any(under(p, h["path"]) for h in hits):   # nested inside an earlier hit
            continue
        parent = os.path.dirname(p)
        try:
            pm = os.stat(parent).st_mtime
        except OSError:
            pm = None
        hits.append({"path": p, "size": m.sizes[p], "parent_mtime": pm,
                     "evidence": dep_evidence(p)})
    hits.sort(key=lambda h: -h["size"])
    data = {"hits": hits, "total": sum(h["size"] for h in hits)}

    findings = [Finding(label=tilde(h["path"]), size=h["size"], path=h["path"],
                        detail="project modified %s; %s" % (
                            date_str(h["parent_mtime"]) if h["parent_mtime"] else "?",
                            h["evidence"]),
                        category=category(h["path"]), mtime=h["parent_mtime"])
                for h in hits]
    shown = data["total"] > THRESHOLD
    return CheckResult(
        severity="info", total=data["total"], findings=findings,
        note="Rebuildable from a lockfile or manifest where one is named; "
             "UNVERIFIED ones only match by folder name.",
        state="found" if shown else "quiet", quiet_label="dependency folders", data=data)


# ---------------------------------------------------------------------------
# 7. APFS snapshots
# ---------------------------------------------------------------------------

@check(7, "snapshots", "APFS SNAPSHOTS")
def snapshots(ctx):
    du_out = ctx.run(["diskutil", "apfs", "listSnapshots", "/System/Volumes/Data"], 30)
    tm_out = ctx.run(["tmutil", "listlocalsnapshots", "/"], 30)
    data_vol = []
    for block in du_out[0].split("+--")[1:]:
        name = re.search(r"Name:\s+(\S+)", block)
        purge = re.search(r"Purgeable:\s+(\w+)", block)
        if name:
            data_vol.append({"name": name.group(1),
                             "purgeable": purge.group(1) if purge else "?"})
    tm_local, update = [], []
    for line in tm_out[0].splitlines():
        line = line.strip()
        if line.startswith("com.apple.TimeMachine."):
            tm_local.append(line)
        elif line.startswith("com.apple.os.update-"):
            update.append(line)
    data = {"data_volume": data_vol, "tm_local": tm_local, "update": update,
            "pins_user_data": bool(data_vol or tm_local)}

    findings = [Finding(label=d["name"], category="SYSTEM",
                        detail="Data volume, pins your deleted files; purgeable: %s"
                        % d["purgeable"]) for d in data_vol]
    names = {d["name"] for d in data_vol}
    findings += [Finding(label=t, detail="Time Machine local snapshot", category="SYSTEM")
                 for t in tm_local if t.replace(" (dataless)", "") not in names]
    if update:
        findings.append(Finding(label="%d system-volume update snapshot(s)" % len(update),
                                detail="pin nothing of yours", category="SYSTEM"))

    pins = data["pins_user_data"]
    return CheckResult(
        severity="warning" if pins else "info", total=0, findings=findings,
        note=("Space from files deleted after a snapshot was taken stays used until the "
              "snapshot is removed. Time Machine local snapshots normally expire within 24 "
              "hours, sooner when space runs low.") if pins else
             "Update snapshots live on the system volume and pin nothing of yours.",
        state="found" if (pins or update) else "quiet", quiet_label="snapshots", data=data)


# ---------------------------------------------------------------------------
# 8. Backup freshness
# ---------------------------------------------------------------------------

def tm_plist_date():
    """Last backup date from Time Machine's settings file, if readable (needs Full Disk Access)."""
    try:
        with open("/Library/Preferences/com.apple.TimeMachine.plist", "rb") as f:
            pl = plistlib.load(f)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None
    dates = []
    for d in pl.get("Destinations", []) or []:
        for v in d.get("SnapshotDates", []) or []:
            if isinstance(v, dt.datetime):
                dates.append(v)
    if not dates:
        return None
    latest = max(dates)
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=dt.timezone.utc)
    return latest.timestamp()


@check(8, "backup", "TIME MACHINE BACKUP")
def backup(ctx):
    latest_out = ctx.run(["tmutil", "latestbackup"], 30)
    dest_out = ctx.run(["tmutil", "destinationinfo"], 30)
    text = (latest_out[0] + "\n" + latest_out[1]).strip()
    dests = re.findall(r"^Name\s*:\s*(.+)$", dest_out[0], re.M)
    no_dest = "No destinations configured" in (dest_out[0] + dest_out[1])
    data = {"date": None, "source": None, "state": None, "destinations": dests,
            "detail": text.splitlines()[-1] if text else ""}
    mt = re.search(r"(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})(\d{2})", text)
    if mt:
        data["date"] = dt.datetime(*map(int, mt.groups())).timestamp()
        data["source"] = "tmutil latestbackup"
        data["state"] = "ok"
    else:
        if no_dest:
            data["state"] = "no destination"
        elif "Full Disk Access" in text:
            data["state"] = "needs full disk access"
        elif re.search(r"mount|not available|unavailable|machine directory", text, re.I):
            data["state"] = "not connected"
        else:
            data["state"] = "unknown"
        pd = tm_plist_date()
        if pd:
            data["date"] = pd
            data["source"] = "Time Machine settings file"
    if data["date"]:
        data["days"] = (ctx.now - data["date"]) / 86400

    stale = data.get("days") is not None and data["days"] > 14
    findings = []
    if data["date"]:
        findings.append(Finding(label="Last backup", mtime=data["date"], category="UNKNOWN",
                                detail=age_str(data["date"], ctx.now)
                                + ("" if data["source"] == "tmutil latestbackup"
                                   else " (from %s)" % data["source"])))
    state_text = {
        "not connected": "Backup drive %s is not connected, so the latest backup date cannot "
                         "be read. That is not the same as having no backup."
                         % (", ".join("'%s'" % n for n in dests) or "the backup drive"),
        "no destination": "Time Machine has no backup destination configured.",
        "needs full disk access": "tmutil needs Full Disk Access to read this.",
        "unknown": "tmutil said: %s" % data["detail"],
    }.get(data["state"])
    if state_text:
        findings.append(Finding(label="Backup drive", detail=state_text, category="UNKNOWN"))

    shown = data["state"] != "ok" or stale
    if stale or data["state"] == "no destination":
        severity = "warning"
    elif shown:
        severity = "notice"
    else:
        severity = "info"
    return CheckResult(
        severity=severity, total=0, findings=findings,
        note="Warns when the last Time Machine backup is more than 14 days old.",
        state="found" if shown else "quiet",
        quiet_label="backup (%d days old)" % int(data["days"]) if not shown else "backup",
        data=data)


# ---------------------------------------------------------------------------
# 9. Apple Intelligence models
# ---------------------------------------------------------------------------

@check(9, "ai_models", "APPLE INTELLIGENCE / UAF MODEL ASSETS")
def ai_models(ctx):
    m = ctx.m
    rows = []
    total = 0
    unreadable_uaf = []
    for p, d, st in entries(ASSETS):
        s = m.size(p, d, st)
        total += s or 0
        if d and "_UAF_" in os.path.basename(p):
            if s is None or m.unreadable_under(p):
                unreadable_uaf.append(os.path.basename(p))
            rows.append({"path": p, "size": s or 0})
    rows.sort(key=lambda r: -r["size"])
    data = {"total": total, "uaf_total": sum(r["size"] for r in rows), "uaf": rows,
            "unreadable": m.unreadable_under(ASSETS), "unreadable_uaf": unreadable_uaf}

    unread = set(unreadable_uaf)
    findings = [Finding(label=os.path.basename(r["path"]),
                        size=None if os.path.basename(r["path"]) in unread else r["size"],
                        path=r["path"],
                        detail="could not be read (Full Disk Access needed)"
                        if os.path.basename(r["path"]) in unread else "",
                        category=category(r["path"])) for r in rows]
    shown = data["uaf_total"] > THRESHOLD
    if shown:
        state = "found"
    elif unreadable_uaf:
        state = "unavailable"
    else:
        state = "quiet"
    r = CheckResult(
        severity="info", total=data["uaf_total"], findings=findings,
        note="SIP-protected. Turning Apple Intelligence off does not reliably release these, "
             "and there is no supported way to remove them. Informational only.",
        state=state, quiet_label="Apple Intelligence models", data=data)
    if not shown and unreadable_uaf:
        r.unavailable = ("Apple Intelligence models (%d folders unreadable; needs Full Disk "
                         "Access)" % len(unreadable_uaf))
    return r


# ---------------------------------------------------------------------------
# 10. Other user accounts
# ---------------------------------------------------------------------------

@check(10, "accounts", "OTHER USER ACCOUNTS")
def accounts(ctx):
    m = ctx.m
    rows = []
    for p, d, st in entries("/Users"):
        if not d or p == HOME or os.path.basename(p) == "Shared":
            continue
        try:
            owner = pwd.getpwuid(st.st_uid).pw_name
        except KeyError:
            owner = "uid %d (no such account)" % st.st_uid
        row = {"path": p, "size": m.sizes.get(p), "owner": owner,
               "unreadable": m.unreadable_under(p)}
        if os.path.basename(p) == "Deleted Users":
            # Archives of deleted accounts: show whose they are rather than "root".
            kids = []
            for q, qd, qst in entries(p):
                if qd:
                    try:
                        kids.append("%s (owner %s)" % (os.path.basename(q),
                                                       pwd.getpwuid(qst.st_uid).pw_name))
                    except KeyError:
                        kids.append("%s (uid %d)" % (os.path.basename(q), qst.st_uid))
            row["contains"] = kids
        rows.append(row)
    rows.sort(key=lambda r: -(r["size"] or 0))
    data = {"accounts": rows}

    findings = []
    for r in rows:
        detail = "owner " + r["owner"]
        if r["unreadable"]:
            detail += "; partial (%d unreadable, sudo for full size)" % r["unreadable"]
        if r.get("contains"):
            detail += "; archived deleted account(s): " + ", ".join(r["contains"])
        findings.append(Finding(label=r["path"], size=r["size"], path=r["path"],
                                detail=detail, category=category(r["path"])))
    shown = any((r["size"] or 0) > THRESHOLD or r["unreadable"] or r["size"] is None
                for r in rows)
    return CheckResult(
        severity="info", total=sum(r["size"] or 0 for r in rows), findings=findings,
        note="Not yours: do not act on these without asking their owner.",
        state="found" if shown else "quiet", quiet_label="other accounts", data=data)
