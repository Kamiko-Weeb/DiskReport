"""Move to Trash: the only change the app makes to your own files. No Qt here.

Nothing is ever erased: items go to the Trash, where Finder's Put Back can restore them, and
only after you confirm a list of exactly what will move. The terminal version
(diskreport.cli) stays read-only and has no equivalent.

What can be trashed is deliberately narrow: an existing path inside your home folder that
is not the home folder itself or one of its standard folders, and whose category is not
SYSTEM, OTHER ACCOUNT or CACHE. Everything else gets Reveal in Finder only.

The checks run on the item's canonical path (its parent folder with symlinks resolved, plus
its own name), compared case-insensitively because the Mac's disk is; the same canonical
path is what gets moved. A symlink is moved as a link, never followed.
"""

import os
import subprocess

from ..core.scanner import HOME, LIB

TRASH_TOOL = "/usr/bin/trash"   # ships with macOS 15 and later; moves a symlink, not its target

# Never offered, even though they are inside ~: removing them breaks the account.
PROTECTED = {HOME, LIB, os.path.join(HOME, ".Trash"), os.path.join(HOME, ".diskreport")} | {
    os.path.join(HOME, n) for n in ("Applications", "Desktop", "Documents", "Downloads",
                                    "Movies", "Music", "Pictures", "Public", "Sites")} | {
    os.path.join(LIB, n) for n in ("Application Support", "Containers", "Group Containers",
                                   "Caches", "Preferences", "Keychains", "Mobile Documents",
                                   "CloudStorage", "Messages", "Mail", "Photos")}
_PROTECTED_CF = {p.casefold() for p in PROTECTED}

BLOCKED_CATEGORIES = {
    "SYSTEM": "SIP-protected: macOS will not let it be removed this way.",
    "OTHER ACCOUNT": "Belongs to another account.",
    "CACHE": "macOS rebuilds caches, so trashing them frees space only briefly.",
}


def canonical(path):
    """The item's real location: parent folder with symlinks and '..' resolved, plus its own
    name (the item itself is not followed if it is a symlink)."""
    path = os.path.normpath(path)
    return os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))


def _under_ci(path, parent):
    p, q = path.casefold(), parent.casefold()
    return p == q or p.startswith(q + "/")


def can_trash(path, category):
    """(allowed, reason if not)."""
    if not path:
        return False, "No path."
    if category in BLOCKED_CATEGORIES:
        return False, BLOCKED_CATEGORIES[category]
    p = canonical(path)
    if not _under_ci(p, HOME) or p.casefold() in _PROTECTED_CF:
        return False, "Outside your own folders, or a folder your account needs."
    if _under_ci(p, os.path.join(HOME, ".Trash")) or _under_ci(p, os.path.join(HOME, ".diskreport")):
        return False, "Already in the Trash, or DiskReport's own data."
    if _under_ci(p, os.path.join(LIB, "Caches")):
        return False, BLOCKED_CATEGORIES["CACHE"]
    if not os.path.lexists(p):
        return False, "No longer exists."
    if not os.path.exists(TRASH_TOOL) and os.path.islink(p):
        # Finder (the fallback below) would follow the link and trash its target.
        return False, "A symbolic link: on this macOS version it can only be moved in Finder."
    return True, ""


def top_level(paths):
    """Drop paths that sit inside another listed path (moving the parent moves them too)."""
    canon = sorted(set(canonical(p) for p in paths), key=lambda p: (len(p), p))
    keep = []
    for p in canon:
        if not any(_under_ci(p, k) for k in keep):
            keep.append(p)
    return keep


def move_to_trash(paths):
    """Move each path to the Trash. Returns [(path, ok, error text)], one per path given.
    Paths inside another listed path move with it and count as moved. Every path is checked
    again immediately before it moves."""
    tops = top_level(paths)
    done = {}
    for p in tops:
        ok, why = can_trash(p, None)
        if not ok:
            done[p] = (False, why)
            continue
        try:
            if os.path.exists(TRASH_TOOL):
                cmd = [TRASH_TOOL, p]
            else:   # older macOS: ask Finder, which also records Put Back (symlinks refused above)
                cmd = ["/usr/bin/osascript", "-e",
                       'tell application "Finder" to delete (POSIX file "%s" as alias)'
                       % p.replace("\\", "\\\\").replace('"', '\\"')]
            r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               stdin=subprocess.DEVNULL, timeout=300)
            moved = not os.path.lexists(p)
            err = (r.stderr.decode("utf-8", "replace").strip() or "the Trash refused it")
            done[p] = (moved, "" if moved else err)
        except (OSError, subprocess.TimeoutExpired) as e:
            done[p] = (False, str(e))

    results = []
    for p in paths:
        c = canonical(p)
        owner = next((t for t in tops if _under_ci(c, t)), c)
        ok, err = done.get(owner, (False, "not processed"))
        results.append((p, ok, err if not ok else ""))
    return results
