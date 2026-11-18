"""Bytes to text, dates, and category labels. Pure functions, no I/O except category(),
which looks for a pyvenv.cfg to recognise a Python venv."""

import datetime as dt
import os

from .scanner import GB, HOME, LIB, MB, under

# Category labels, in the order a legend shows them. Conservative: UNKNOWN unless sure.
CATEGORIES = {
    "CACHE": "macOS rebuilds it",
    "REBUILDABLE": "can be re-created",
    "SYSTEM": "SIP-protected",
    "USER DATA": "your files, be careful",
    "OTHER ACCOUNT": "someone else's",
    "UNKNOWN": "not sure",
}


def fmt(n):
    """Bytes as text: GB with one decimal from 1 GB up, whole MB below."""
    if n is None:
        return "?"
    if abs(n) >= GB - MB / 2:      # so 1023.6 MB shows as 1.0 GB, not 1024 MB
        return "%.1f GB" % (n / GB)
    return "%d MB" % round(n / MB)


def fmt_signed(n):
    return ("+" if n >= 0 else "-") + fmt(abs(n))


def tilde(path):
    if path == HOME:
        return "~"
    if path.startswith(HOME + "/"):
        return "~" + path[len(HOME):]
    return path


def shorten(s, width):
    if len(s) <= width:
        return s
    keep = width - 3
    return s[: keep // 2] + "..." + s[-(keep - keep // 2):]


def date_str(ts):
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def age_str(ts, now):
    days = (now - ts) / 86400
    if days < 1:
        return "%s (today)" % date_str(ts)
    return "%s (%d days ago)" % (date_str(ts), int(days))


USER_DATA_ROOTS = [os.path.join(HOME, x) for x in (
    "Documents", "Desktop", "Downloads", "Pictures", "Movies", "Music",
    "Library/Messages", "Library/Mobile Documents", "Library/Mail",
    "Library/CloudStorage", "Library/Application Support/MobileSync")]


def category(path):
    """One of CATEGORIES for a path. Labels UNKNOWN rather than guessing."""
    if under(path, "/System") or under(path, "/Library/Updates"):
        return "SYSTEM"
    if under(path, "/Users") and not under(path, HOME):
        if under(path, "/Users/Shared"):
            return "UNKNOWN"
        return "OTHER ACCOUNT"
    if under(path, os.path.join(LIB, "Caches")):
        return "CACHE"
    if under(path, HOME) and not under(path, LIB):
        if os.path.basename(path) in ("__pycache__", "node_modules"):
            return "REBUILDABLE"
        if os.path.isfile(os.path.join(path, "pyvenv.cfg")):   # a Python venv
            return "REBUILDABLE"
    if any(under(path, r) for r in USER_DATA_ROOTS):
        return "USER DATA"
    if os.path.dirname(path) == HOME:
        name = os.path.basename(path)
        if not name.startswith(".") and name not in ("Library", "Applications", "Public"):
            return "USER DATA"
    return "UNKNOWN"
