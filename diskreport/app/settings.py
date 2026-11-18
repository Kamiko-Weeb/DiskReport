"""Preferences, the open-at-login LaunchAgent, and how the app was launched.

Preferences live in ~/.diskreport/settings.json, next to the snapshots, rather than in
~/Library/Preferences, so everything the app keeps is in one folder you can inspect.
No Qt in this module.
"""

import json
import os
import plistlib
import sys
import tempfile

from . import BUNDLE_ID
from ..core.scanner import HOME

APP_DIR = os.path.join(HOME, ".diskreport")
SETTINGS_PATH = os.path.join(APP_DIR, "settings.json")
LAUNCH_AGENT = os.path.join(HOME, "Library", "LaunchAgents", BUNDLE_ID + ".plist")

SCHEDULES = [  # (days between automatic scans, label); 0 = off
    (0, "Off"),
    (1, "Daily"),
    (7, "Weekly"),
    (14, "Every 2 weeks"),
    (30, "Monthly"),
]

DEFAULTS = {
    "schedule_days": 7,        # automatic background scan; 0 turns it off
    "notifications": True,     # notify only on threshold crossings (see state.notify_reasons)
}

RESCAN_ON_LAUNCH_DAYS = 7      # scan on launch if the newest snapshot is older than this


def _dir_is_safe():
    """Never write through a symlinked ~/.diskreport to somewhere else."""
    return not os.path.lexists(APP_DIR) or os.path.realpath(APP_DIR) == APP_DIR


def load():
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS_PATH) as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k in DEFAULTS:
                if k in data and isinstance(data[k], type(DEFAULTS[k])):
                    s[k] = data[k]
    except (OSError, ValueError):
        pass
    return s


def save(s):
    """Write settings atomically. Returns False if ~/.diskreport is not safe to write."""
    if not _dir_is_safe():
        return False
    os.makedirs(APP_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".settings-", dir=APP_DIR)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump({k: s.get(k, v) for k, v in DEFAULTS.items()}, f, indent=1)
        os.replace(tmp, SETTINGS_PATH)
    except BaseException:
        os.remove(tmp)   # our own temp file
        raise
    return True


# ---------------------------------------------------------------------------
# How the app is running
# ---------------------------------------------------------------------------

def is_frozen():
    """True when running from the packaged DiskReport.app."""
    return bool(getattr(sys, "frozen", False))


def app_bundle_path():
    """/path/to/DiskReport.app when packaged, else None."""
    if not is_frozen():
        return None
    p = os.path.abspath(sys.executable)
    for _ in range(4):
        p = os.path.dirname(p)
        if p.endswith(".app"):
            return p
    return None


def is_translocated():
    """macOS runs a quarantined app that was opened from Downloads (or a disk image) from a
    random read-only copy ("App Translocation"). It works, but open-at-login would point at
    a path that disappears. The fix is moving the app to Applications."""
    p = app_bundle_path() or ""
    return "/AppTranslocation/" in p


def is_on_disk_image():
    """Running straight from the read-only mounted .dmg (an app on any writable volume is fine)."""
    p = app_bundle_path()
    if not p:
        return False
    try:
        return bool(os.statvfs(p).f_flag & os.ST_RDONLY)
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Open at login
#
# A user LaunchAgent that runs `open -b <bundle id>`, so it keeps working if the app is
# moved. Written only when the user turns the preference on, removed when they turn it off.
# ---------------------------------------------------------------------------

def login_item_supported():
    """(supported, reason if not)."""
    if not is_frozen():
        return False, "Available in the packaged app, not when running from source."
    if is_translocated() or is_on_disk_image():
        return False, "Move DiskReport to your Applications folder first."
    return True, ""


def launch_at_login_enabled():
    try:
        with open(LAUNCH_AGENT, "rb") as f:
            return plistlib.load(f).get("Label") == BUNDLE_ID
    except (OSError, plistlib.InvalidFileException, ValueError, AttributeError):
        return False


def set_launch_at_login(enabled):
    """Create or remove this app's own LaunchAgent. Removes only a file whose Label is ours."""
    if enabled:
        folder = os.path.dirname(LAUNCH_AGENT)
        os.makedirs(folder, exist_ok=True)
        # Temp file + rename: a failed write leaves nothing half-written behind, and a
        # symlink at the final path is replaced rather than followed.
        fd, tmp = tempfile.mkstemp(prefix=".%s-" % BUNDLE_ID, dir=folder)
        try:
            with os.fdopen(fd, "wb") as f:
                plistlib.dump({"Label": BUNDLE_ID,
                               "ProgramArguments": ["/usr/bin/open", "-g", "-b", BUNDLE_ID,
                                                    "--args", "--background"],
                               "RunAtLoad": True}, f)
            os.replace(tmp, LAUNCH_AGENT)
        except BaseException:
            os.remove(tmp)   # our own temp file
            raise
    elif launch_at_login_enabled() and not os.path.islink(LAUNCH_AGENT):
        os.remove(LAUNCH_AGENT)   # our own LaunchAgent, identified by its Label
